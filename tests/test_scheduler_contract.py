from synthesizer.scheduler import SynthesisScheduler


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


def test_scheduler_declares_desktop_and_usage_jobs():
    scheduler = object.__new__(SynthesisScheduler)
    scheduler.cfg = DictConfig(
        {
            "synthesizer": {
                "schedule": {"enabled": False},
                "periodic_checkpoint": {"enabled": True},
            },
            "notifiers": {
                "desktop": {"enabled": True},
                "telegram": {"enabled": False},
            },
            "usage_tracking": {
                "enabled": True,
                "notifications": {"enabled": True},
            },
        }
    )
    assert scheduler.configured_job_ids() == [
        "coverage_ledger_job",
        "desktop_evening_job",
        "desktop_morning_job",
        "periodic_checkpoint_job",
        "usage_milestone_job",
    ]


def test_clock_parser_rejects_invalid_values():
    assert SynthesisScheduler._parse_clock("25:90", (8, 30)) == (8, 30)
    assert SynthesisScheduler._parse_clock("07:15", (8, 30)) == (7, 15)


def test_scheduler_has_single_backend():
    """APScheduler 是硬依賴；2026-09-16 前有一段永遠跑不到的手刻備援排程會靜默漂移（TODO B7）。"""
    assert not hasattr(SynthesisScheduler, "_std_scheduler_loop")
    scheduler = object.__new__(SynthesisScheduler)
    scheduler._apscheduler = None
    assert scheduler.backend_name() == "stopped"
    assert scheduler.active_job_ids() == []
