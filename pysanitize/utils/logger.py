import os
import sys
import logging
import colorlog

# ---------- 1) custom SUCCESS level ----------
SUCCESS_LEVEL_NUM = 25
logging.addLevelName(SUCCESS_LEVEL_NUM, "SUCCESS")

def success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS_LEVEL_NUM):
        self._log(SUCCESS_LEVEL_NUM, message, args, **kwargs)

logging.Logger.success = success

# ---------- 2) filters that split stdout / stderr ----------
class MaxLevelFilter(logging.Filter):
    """Pass records below ``max_level`` only (for stdout)."""
    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.max_level

class MinLevelFilter(logging.Filter):
    """Pass records at ``min_level`` or above only (for stderr)."""
    def __init__(self, min_level):
        super().__init__()
        self.min_level = min_level
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.min_level

# ---------- 3) loguru-like ColoredFormatter ----------
def _make_colored_formatter():
    # color map (loguru feel: DEBUG/INFO cool tones, WARNING yellow,
    # ERROR/CRITICAL red, SUCCESS green)
    log_colors = {
        "DEBUG":    "blue",
        "INFO":     "white",
        "SUCCESS":  "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "red,bg_white",
    }
    # secondary coloring: levelname/message tinted by level; name/func/line cyan
    secondary = {
        "levelname": log_colors,
        "message":   {
            "DEBUG":    "blue",
            "INFO":     "white",
            "SUCCESS":  "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "red",
        },
        # these are fixed cyan, mimicking loguru's <cyan> tags
        "name":      {k: "cyan" for k in log_colors},
        "funcName":  {k: "cyan" for k in log_colors},
        "lineno":    {k: "cyan" for k in log_colors},
        "asctime":   {k: "green" for k in log_colors},  # timestamp in green
    }


    return colorlog.ColoredFormatter(
        fmt=(
            "%(asctime_log_color)s%(asctime)s.%(msecs)03d%(reset)s"
            " | %(levelname_log_color)s%(levelname)-8s%(reset)s"
            " | %(name_log_color)s%(name)s%(reset)s"
            ":%(funcName_log_color)s%(filename)s%(reset)s"
            ":%(funcName_log_color)s%(funcName)s%(reset)s"
            ":%(lineno_log_color)s%(lineno)d%(reset)s"
            " - %(message_log_color)s%(message)s%(reset)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors=log_colors,
        secondary_log_colors=secondary,
        style="%",
    )

# ---------- 4) get_logger (mirrors loguru's add behaviour) ----------
def get_logger(level: str = None) -> logging.Logger:
    """Return a logger named 'PySanitize':
    - console output is split: <ERROR to stdout; >=ERROR to stderr
    - colors and format aim to match loguru's defaults
    - never adds a duplicate handler
    """
    if level is None:
        level = os.getenv("PYSANITIZE_LOGGING_LEVEL", "INFO")

    logger = logging.getLogger("PySanitize")
    logger.setLevel(level)
    logger.propagate = False  # avoid duplicate output through the root logger

    if logger.handlers:
        # already initialized — return it (swap for an update of level/format
        # if you ever need to reconfigure at runtime)
        return logger

    colored_fmt = _make_colored_formatter()

    # stdout: DEBUG/INFO/SUCCESS/WARNING
    h_out = logging.StreamHandler(stream=sys.stdout)
    h_out.setLevel(level)
    h_out.addFilter(MaxLevelFilter(logging.ERROR))
    h_out.setFormatter(colored_fmt)

    # stderr: ERROR/CRITICAL
    h_err = logging.StreamHandler(stream=sys.stderr)
    h_err.setLevel(level)
    h_err.addFilter(MinLevelFilter(logging.ERROR))
    h_err.setFormatter(colored_fmt)

    logger.addHandler(h_out)
    logger.addHandler(h_err)
    return logger