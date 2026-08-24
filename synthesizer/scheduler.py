import logging
import threading
import time
from datetime import datetime
from core.config import get_config
from .aggregator import generate_daily_summary_pipeline, generate_periodic_checkpoint
from notifiers.telegram_notifier import TelegramNotifier
from notifiers.desktop_notifier import DesktopNotifier
from exporters.daily_brief import export_daily_brief
from core.usage_analytics import evaluate_daily_milestones

logger = logging.getLogger("OmniContext.Scheduler")


class SynthesisScheduler:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._apscheduler = None
        self._notifier = TelegramNotifier()
        self._desktop = DesktopNotifier()

    def start(self):
        daily_enabled = self.cfg.get("synthesizer.schedule.enabled", True)
        checkpoint_enabled = self.cfg.get("synthesizer.periodic_checkpoint.enabled", True)
        telegram_enabled = self.cfg.get("notifiers.telegram.enabled", False)
        desktop_enabled = self.cfg.get("notifiers.desktop.enabled", True)
        usage_milestones_enabled = (
            self.cfg.get("usage_tracking.enabled", False)
            and self.cfg.get("usage_tracking.notifications.enabled", False)
        )

        if not daily_enabled and not checkpoint_enabled and not telegram_enabled and not desktop_enabled and not usage_milestones_enabled:
            logger.info("Schedulers are disabled in config.")
            return

        time_str = self.cfg.get("synthesizer.schedule.time", "23:30")
        try:
            hour_str, min_str = time_str.split(":")
            hour = int(hour_str)
            minute = int(min_str)
        except Exception:
            hour, minute = 23, 30

        interval_hours = self.cfg.get("synthesizer.periodic_checkpoint.interval_hours", 2)

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._apscheduler = BackgroundScheduler()
            
            if daily_enabled:
                self._apscheduler.add_job(
                    func=self._run_daily_job,
                    trigger="cron",
                    hour=hour,
                    minute=minute,
                    id="daily_synthesis_job",
                    replace_existing=True
                )
                logger.info(f"Daily synthesis scheduled for {hour:02d}:{minute:02d} daily.")

            if checkpoint_enabled:
                self._apscheduler.add_job(
                    func=self._run_checkpoint_job,
                    trigger="interval",
                    hours=interval_hours,
                    id="periodic_checkpoint_job",
                    replace_existing=True
                )
                logger.info(f"Periodic checkpoint log scheduled every {interval_hours} hours.")

            if telegram_enabled:
                # 晨報 (預設 09:00)
                m_time = self.cfg.get("notifiers.telegram.morning_briefing_time", "09:00")
                m_h, m_m = [int(x) for x in m_time.split(":")]
                self._apscheduler.add_job(
                    func=self._run_morning_briefing_job,
                    trigger="cron",
                    hour=m_h,
                    minute=m_m,
                    id="morning_briefing_job",
                    replace_existing=True
                )
                logger.info(f"Morning briefing scheduled for {m_h:02d}:{m_m:02d} daily.")

            # 桌面通知：早報與晚報（不需任何帳號或金鑰）
            if self.cfg.get("notifiers.desktop.enabled", True):
                for job_id, time_key, default_time, func in (
                    ("desktop_morning_job", "notifiers.desktop.morning_briefing_time", "08:30", self._run_desktop_morning_job),
                    ("desktop_evening_job", "notifiers.desktop.evening_summary_time", "22:00", self._run_desktop_evening_job),
                ):
                    try:
                        d_h, d_m = [int(x) for x in str(self.cfg.get(time_key, default_time)).split(":")]
                    except Exception:
                        d_h, d_m = [int(x) for x in default_time.split(":")]

                    self._apscheduler.add_job(
                        func=func, trigger="cron", hour=d_h, minute=d_m,
                        id=job_id, replace_existing=True
                    )
                    logger.info(f"Desktop notification '{job_id}' scheduled for {d_h:02d}:{d_m:02d} daily.")

            if usage_milestones_enabled:
                usage_interval = max(
                    5,
                    int(self.cfg.get("usage_tracking.notifications.check_interval_minutes", 15)),
                )
                self._apscheduler.add_job(
                    func=self._run_usage_milestone_job,
                    trigger="interval",
                    minutes=usage_interval,
                    id="usage_milestone_job",
                    replace_existing=True,
                )
                logger.info(
                    f"Usage milestone evaluation scheduled every {usage_interval} minutes."
                )

            self._apscheduler.start()
        except ImportError:
            # 原生執行緒排程備援機制
            self._running = True
            self._thread = threading.Thread(
                target=self._std_scheduler_loop,
                args=(
                    hour,
                    minute,
                    interval_hours,
                    daily_enabled,
                    checkpoint_enabled,
                    telegram_enabled,
                    desktop_enabled,
                    usage_milestones_enabled,
                ),
                daemon=True
            )
            self._thread.start()
            logger.info("Synthesis scheduler (Built-in Timer) started.")

    def _std_scheduler_loop(
        self,
        target_hour: int,
        target_minute: int,
        interval_hours: int,
        daily_enabled: bool,
        cp_enabled: bool,
        telegram_enabled: bool,
        desktop_enabled: bool,
        usage_milestones_enabled: bool,
    ):
        last_executed_day = None
        last_cp_time = time.time()
        last_usage_time = 0.0
        last_desktop_morning_day = None
        last_desktop_evening_day = None
        last_telegram_morning_day = None

        desktop_morning = self._parse_clock(
            self.cfg.get("notifiers.desktop.morning_briefing_time", "08:30"),
            (8, 30),
        )
        desktop_evening = self._parse_clock(
            self.cfg.get("notifiers.desktop.evening_summary_time", "22:00"),
            (22, 0),
        )
        telegram_morning = self._parse_clock(
            self.cfg.get("notifiers.telegram.morning_briefing_time", "09:00"),
            (9, 0),
        )

        while self._running:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            # 每日排程
            if daily_enabled and now.hour == target_hour and now.minute == target_minute and last_executed_day != today_str:
                last_executed_day = today_str
                self._run_daily_job()
            
            # 週期性快照
            if cp_enabled and (time.time() - last_cp_time) >= (interval_hours * 3600):
                last_cp_time = time.time()
                self._run_checkpoint_job()

            if usage_milestones_enabled:
                usage_interval = max(
                    5,
                    int(self.cfg.get("usage_tracking.notifications.check_interval_minutes", 15)),
                )
                if (time.time() - last_usage_time) >= usage_interval * 60:
                    last_usage_time = time.time()
                    self._run_usage_milestone_job()

            if (
                desktop_enabled
                and (now.hour, now.minute) == desktop_morning
                and last_desktop_morning_day != today_str
            ):
                last_desktop_morning_day = today_str
                self._run_desktop_morning_job()

            if (
                desktop_enabled
                and (now.hour, now.minute) == desktop_evening
                and last_desktop_evening_day != today_str
            ):
                last_desktop_evening_day = today_str
                self._run_desktop_evening_job()

            if (
                telegram_enabled
                and (now.hour, now.minute) == telegram_morning
                and last_telegram_morning_day != today_str
            ):
                last_telegram_morning_day = today_str
                self._run_morning_briefing_job()

            time.sleep(30)

    @staticmethod
    def _parse_clock(value, fallback: tuple[int, int]) -> tuple[int, int]:
        try:
            hour, minute = [int(part) for part in str(value).split(":", 1)]
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except (TypeError, ValueError):
            pass
        return fallback

    def configured_job_ids(self) -> list[str]:
        jobs = []
        if self.cfg.get("synthesizer.schedule.enabled", True):
            jobs.append("daily_synthesis_job")
        if self.cfg.get("synthesizer.periodic_checkpoint.enabled", True):
            jobs.append("periodic_checkpoint_job")
        if self.cfg.get("notifiers.telegram.enabled", False):
            jobs.append("morning_briefing_job")
        if self.cfg.get("notifiers.desktop.enabled", True):
            jobs.extend(["desktop_morning_job", "desktop_evening_job"])
        if (
            self.cfg.get("usage_tracking.enabled", False)
            and self.cfg.get("usage_tracking.notifications.enabled", False)
        ):
            jobs.append("usage_milestone_job")
        return sorted(jobs)

    def active_job_ids(self) -> list[str]:
        if self._apscheduler and getattr(self._apscheduler, "running", False):
            return sorted(job.id for job in self._apscheduler.get_jobs())
        if self._thread and self._thread.is_alive():
            return self.configured_job_ids()
        return []

    def backend_name(self) -> str:
        if self._apscheduler and getattr(self._apscheduler, "running", False):
            return "apscheduler"
        if self._thread and self._thread.is_alive():
            return "builtin_timer"
        return "stopped"

    def _run_daily_job(self):
        logger.info("Triggering scheduled daily synthesis...")
        try:
            res = generate_daily_summary_pipeline()
            logger.info(f"Daily synthesis finished: {res.get('status')}")

            # 日報產生後未結事項會更新，順道刷新每日入口的簡報檔
            self._refresh_daily_brief()

            # 若啟用 Telegram，自動推播晚報
            if self._notifier.is_enabled() and "date_str" in res:
                self._notifier.send_daily_summary(res["date_str"])
                # 順道檢查停滯專案
                self._notifier.send_stagnation_alert()
        except Exception as e:
            logger.error(f"Error during scheduled daily synthesis: {e}", exc_info=True)

    def _run_checkpoint_job(self):
        logger.info("Triggering scheduled periodic checkpoint log...")
        try:
            interval_hours = self.cfg.get("synthesizer.periodic_checkpoint.interval_hours", 2)
            res = generate_periodic_checkpoint(hours=interval_hours)
            logger.info(f"Periodic checkpoint generated: {res.get('file_name')}")
        except Exception as e:
            logger.error(f"Error generating checkpoint log: {e}", exc_info=True)

    def _refresh_daily_brief(self):
        """更新每日入口的簡報檔案（早晚報前都先刷新一次，確保通知與檔案一致）"""
        if not self.cfg.get("exporters.daily_brief.enabled", True):
            return
        try:
            res = export_daily_brief()
            logger.info(f"Daily brief exported: {res.get('active_count')} active projects, {res.get('open_loops_count')} open loops.")
        except Exception as e:
            logger.error(f"Error exporting daily brief: {e}", exc_info=True)

    def _run_desktop_morning_job(self):
        logger.info("Triggering desktop morning briefing...")
        self._refresh_daily_brief()
        try:
            self._desktop.send_morning_briefing()
            self._desktop.send_stagnation_alert()
        except Exception as e:
            logger.error(f"Error sending desktop morning briefing: {e}", exc_info=True)

    def _run_desktop_evening_job(self):
        logger.info("Triggering desktop evening summary...")
        self._refresh_daily_brief()
        try:
            self._desktop.send_evening_summary()
        except Exception as e:
            logger.error(f"Error sending desktop evening summary: {e}", exc_info=True)

    def _run_usage_milestone_job(self):
        logger.info("Evaluating daily interface usage milestones...")
        try:
            result = evaluate_daily_milestones(notifier=self._desktop)
            logger.info(f"Usage milestone evaluation: {result.get('status')}")
        except Exception as e:
            logger.error(f"Error evaluating usage milestone: {e}", exc_info=True)

    def _run_morning_briefing_job(self):
        logger.info("Triggering scheduled morning briefing...")
        try:
            if self._notifier.is_enabled():
                self._notifier.send_morning_briefing()
        except Exception as e:
            logger.error(f"Error sending morning briefing: {e}", exc_info=True)

    def shutdown(self):
        if self._apscheduler and self._apscheduler.running:
            self._apscheduler.shutdown()
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("Synthesis scheduler stopped.")
