"""Arka plan güncellemesi için başlatıcı: `python -m kbu_mcp.bgupdate <veri_klasörü> [--hot]`.

Windows'ta WMI ile başlatılan süreç ortam değişkenlerini miras almaz; bu yüzden veri klasörü argüman olarak
gelir ve config içe aktarılmadan önce ayarlanır. Çıktı veri klasöründeki update.log dosyasına yazılır.
"""

import os
import sys


def main() -> None:
    data_dir = sys.argv[1]
    hot = "--hot" in sys.argv[2:]
    os.environ["KBU_DATA_DIR"] = data_dir
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.makedirs(data_dir, exist_ok=True)
    log = open(os.path.join(data_dir, "update.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log

    from .cli import main as cli_main

    cli_main(["update"] + (["--hot"] if hot else []))


if __name__ == "__main__":
    main()
