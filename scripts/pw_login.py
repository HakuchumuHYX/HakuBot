"""完美账号重新登录：把新 token 写进 config/cs_stats/config.json。

先在完美 App 登录页给手机发验证码（不要在 App 里提交），然后在项目根目录运行：
    uv run python scripts/pw_login.py
写入后在群里或私聊发 SU 命令「完美重载」生效，不用重启 bot。
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.settings import update_fields  # noqa: E402
from utils.json_io import load_json  # noqa: E402
from utils.paths import PluginPaths  # noqa: E402

CONFIG_FILE = PluginPaths("cs_stats").config / "config.json"


def main():
    appversion = load_json(CONFIG_FILE).data["pw_appversion"]
    mobile = input("手机号: ").strip()
    code = input("验证码: ").strip()
    request = urllib.request.Request(
        "https://passport.pwesports.cn/account/login",
        json.dumps({"appId": 2, "mobilePhone": mobile, "securityCode": code}).encode(),
        {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "okhttp/4.11.0",
            "appversion": appversion,
            "platform": "android",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if str(data.get("code")) != "0":
        sys.exit(f"登录失败: {data.get('description') or data}")
    account = data["result"]["loginResult"]["accountInfo"]
    update_fields(CONFIG_FILE, {"pw_token": account["token"], "pw_steam_id": str(account["steamId"])})
    print(f"已写入 {CONFIG_FILE}，SteamID {account['steamId']}。发「完美重载」生效。")


if __name__ == "__main__":
    main()
