import logging
import threading
import time
from datetime import datetime
from core.config import get_config
from .aggregator import generate_daily_summary_pipeline, generate_periodic_checkpoint
from notifiers.telegram_notifier import TelegramNotifier

logger = logging.getLogger("OmniContext.Scheduler")


class SynthesisScheduler:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._apscheduler = None
        self._notifier = TelegramNotifier()

    def start(self):
        daily_enabled = self.cfg.get("synthesizer.schedule.enabled", True)
        checkpoint_enabled = self.cfg.get("synthesizer.periodic_checkpoint.enabled", True)
        telegram_enabled = self.cfg.get("notifiers.telegram.enabled", False)

        if not daily_enabled and not checkpoint_enabled and not telegram_enabled:
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

            self._apscheduler.start()
        except ImportError:
            # 原生執行緒排程備援機制
            self._running = True
            self._thread = threading.Thread(
                target=self._std_scheduler_loop,
                args=(hour, minute, interval_hours, daily_enabled, checkpoint_enabled),
                daemon=True
            )
            self._thread.start()
            logger.info("Synthesis scheduler (Built-in Timer) started.")

    def _std_scheduler_loop(self, target_hour: int, target_minute: int, interval_hours: int, daily_enabled: bool, cp_enabled: bool):
        last_executed_day = None
        last_cp_time = time.time()

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

            time.sleep(30)

    def _run_daily_job(self):
        logger.info("Triggering scheduled daily synthesis...")
        try:
            res = generate_daily_summary_pipeline()
            logger.info(f"Daily synthesis finished: {res.get('status')}")

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
