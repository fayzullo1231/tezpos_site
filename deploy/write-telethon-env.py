#!/usr/bin/env python3
"""Serverda: python3 deploy/write-telethon-env.py
Lokal .env dagi TELETHON_* qiymatlarini shu faylga qo'ying yoki
pastdagi DEFAULT_* larni to'ldiring.
"""
from pathlib import Path

ENV_PATH = Path("/opt/tezpos_site/.env")

# Serverda ishlatish uchun qiymatlar
TELETHON_API_ID = "32368794"
TELETHON_API_HASH = "b90821d1c27f7ae680f10f569aabc056"
TELETHON_SESSION = (
    "1ApWapzMBu1RyDbnQx964rAQesGoPDXC34omH13iIjz_AtFy9fEbQu12xgDgho3D"
    "Mi50JuHfbFi8RoYBlZksK0sr-pSklxmkmIF6nU7Y1-Fka93sFTBMl_DpBHQ2dGxu"
    "SlddaiulfPqGC8rEQI6jtKo7oyopLcJ3t3WXdgv9TwA8MHOEZgXY5q5LRjhwB3cv"
    "CUrbl2zttMtiK56wE7BSkeZclUsDtIuqYaps224eFwcf587BPZDsm5KlnvXFvmui"
    "qD0b_jTkD5Cga_qEwL7WDuEJagtBkvjAZN1yz6MF08MtjYWrgwfz6ai6vFUyXjsj"
    "Zt4DkU3oPgF5RtxT_fKiXQV0Yh_GzBYg="
)


def main() -> None:
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ENV_PATH.exists():
        lines = [
            ln
            for ln in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
            if not ln.strip().startswith("TELETHON_")
        ]
    else:
        lines = []
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(
        [
            "",
            "# Qarz Telegram (Telethon)",
            f"TELETHON_API_ID={TELETHON_API_ID}",
            f"TELETHON_API_HASH={TELETHON_API_HASH}",
            # systemd EnvironmentFile uchun qo'shtirnoq
            f'TELETHON_SESSION="{TELETHON_SESSION}"',
            "",
        ]
    )
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    try:
        ENV_PATH.chmod(0o640)
    except OSError:
        pass
    print(f"OK: wrote {ENV_PATH}")
    print(f"TELETHON_API_ID={TELETHON_API_ID}")
    print(f"TELETHON_API_HASH={TELETHON_API_HASH[:8]}...")
    print(f"TELETHON_SESSION len={len(TELETHON_SESSION)}")
    print("Eslatma: chown www-data:www-data .env && systemctl restart tezpos-site")


if __name__ == "__main__":
    main()
