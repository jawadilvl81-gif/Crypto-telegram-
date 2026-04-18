import subprocess
import sys
import time
import logging
from datetime import datetime
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - watchdog - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(config.HEALTH_LOG), logging.StreamHandler()]
)
logger = logging.getLogger("watchdog")

def main():
    while True:
        logger.info("启动主机器人进程...")
        proc = subprocess.Popen([sys.executable, "main.py"])
        proc.wait()
        logger.error(f"主进程退出，代码 {proc.returncode}，10秒后重启")
        time.sleep(10)

if __name__ == "__main__":
    main()
