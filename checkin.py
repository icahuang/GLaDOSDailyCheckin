import os
import re

import requests

# 接口定义取自 GLaDOS 线上前端 bundle（app.bundle.js / static/js/*.chunk.js）：
#   POST {base}/api/user/checkin   body: {"token": <hostname>}
#   baseURL = "/api"，鉴权只依赖浏览器 Cookie(koa:sess / koa:sess.sig)，
#   前端没有额外携带 Authorization 或设备指纹请求头。
CHECKIN_PATH = "/api/user/checkin"
STATUS_PATH = "/api/user/status"

# Cookie 是按域名下发的，只有签发它的域名才认。三个已知可用的控制台域名都试一遍，
# 任一成功即结束——这样即使 Cookie 是迁移前在旧域名上取的，也还能签上。
BASE_URLS = [
    "https://glados.cloud",
    "https://glados.one",
    "https://glados.network",
]

# GLaDOS 新增了「设备绑定」：签到会校验请求的浏览器指纹是否与登录设备一致。
# 这里默认与取 Cookie 时使用的 Chrome 保持一致，也可通过环境变量覆盖。
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

# GLaDOS 对「未认证」请求统一返回 code=-2 没有权限（没带 Cookie 和 Cookie 失效
# 的返回完全一样），所以拿到 -2 时继续换域名重试没有任何意义。
AUTH_ERROR_CODE = -2

# 消息里出现这些字样说明今天已经签过了，属于成功路径而非失败。
ALREADY_CHECKED_IN_HINTS = ("已经签到", "已签到", "already")


def normalize_cookie(raw):
    """把从浏览器/Secrets 复制来的 Cookie 规整成合法的 Cookie 头。

    手工粘贴 Cookie 时最常见的几个坑：带上了 "Cookie: " 前缀、首尾有引号、
    分号前后混入换行。这些都会让服务端解析不到 koa:sess 而直接返回 -2。
    """
    value = raw.strip().strip('"').strip("'").strip()
    if value.lower().startswith("cookie:"):
        value = value[len("cookie:"):]
    items = [item.strip() for item in re.split(r"[;\r\n]+", value) if item.strip()]
    return "; ".join(items)


def parse_response(response):
    """返回值 (code, message, payload)。解析失败时 code 为 None。"""
    try:
        payload = response.json()
    except ValueError:
        return None, f"接口返回非 JSON: {response.text[:300]}", {}
    if not isinstance(payload, dict):
        return None, f"接口返回格式异常: {payload}", {}
    code = payload.get("code")
    message = payload.get("message") or payload.get("msg") or str(payload)
    return code, message, payload


def describe_failure(code, message, payload):
    """把 GLaDOS 的错误码翻译成可以直接照做的排查建议。"""
    if code == AUTH_ERROR_CODE:
        return (
            "    Cookie 已失效或不被识别（GLaDOS 对未认证请求统一返回 code=-2 没有权限）。\n"
            "    处理办法：浏览器重新登录 https://glados.cloud，在开发者工具\n"
            "    Network 里任意 /api/ 请求 -> Request Headers -> 复制完整的 Cookie 值，\n"
            "    更新仓库 Secrets 的 GLADOS_COOKIE（不要带引号、前缀和换行），再重新运行。"
        )
    if code == 4 and payload.get("reason") == "device-mismatch":
        return (
            "    签到被设备绑定拦截（code=4 device-mismatch）：当前请求的浏览器指纹与登录设备不一致。\n"
            "    处理办法：浏览器退出登录后重新登录，再取一次 Cookie；\n"
            "    若仍然失败，把 GLADOS_USER_AGENT 设为与取 Cookie 时相同的浏览器 UA。\n"
            f"    登录设备: {payload.get('loginDevice')} / 当前设备: {payload.get('currentDevice')}"
        )
    return f"签到失败: code={code}, message={message}"


def attempt(base, cookie, user_agent):
    """在单个域名上尝试签到。失败抛 RuntimeError，成功直接返回。"""
    # token 必须与访问域名一致，服务端拿它与 Host 做比对
    hostname = base.replace("https://", "").replace("http://", "").rstrip("/")
    headers = {
        "cookie": cookie,
        "referer": f"{base}/console/checkin",
        "origin": base,
        "user-agent": user_agent,
        "content-type": "application/json;charset=UTF-8",
        "accept": "application/json, text/plain, */*",
    }

    session = requests.Session()
    response = session.post(
        f"{base}{CHECKIN_PATH}",
        headers=headers,
        json={"token": hostname},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"签到接口 HTTP {response.status_code}: {response.text[:300]}")

    code, message, payload = parse_response(response)
    print(f"[{base}] 签到返回: code={code}, message={message}")

    if code != 0:
        raise RuntimeError(describe_failure(code, message, payload))

    # 查询余额/天数状态。这一步只是展示信息，失败不应让签到被判为失败。
    try:
        status_res = session.get(f"{base}{STATUS_PATH}", headers=headers, timeout=30)
        status_code, status_message, status_payload = parse_response(status_res)
        if status_code == 0:
            left_days = int(float(status_payload["data"]["leftDays"]))
            print(f"剩余会员天数: {left_days} 天")
        else:
            print(f"[{base}] 状态接口返回异常: code={status_code}, message={status_message}")
    except Exception as e:
        print(f"[{base}] 状态查询失败（不影响签到）: {e}")


def glados_checkin():
    raw_cookie = os.environ.get("GLADOS_COOKIE")
    if not raw_cookie:
        raise SystemExit("未找到 GLADOS_COOKIE，请检查 Secrets 配置。")

    cookie = normalize_cookie(raw_cookie)
    cookie_names = [item.split("=", 1)[0].strip() for item in cookie.split("; ")]
    # 只打印字段名，不打印值，避免 Cookie 泄漏到 Actions 日志
    print(f"Cookie 字段: {', '.join(cookie_names)}")
    if not any(name.startswith("koa:sess") for name in cookie_names):
        print("警告: Cookie 中缺少 koa:sess，几乎可以确定会在服务端认证失败。")

    user_agent = os.environ.get("GLADOS_USER_AGENT") or DEFAULT_USER_AGENT

    errors = []
    for base in BASE_URLS:
        try:
            attempt(base, cookie, user_agent)
            # 成功则不再尝试其它域名
            return
        except Exception as e:
            errors.append((base, str(e)))

    # 三个域名往往报同一个错（比如都是 Cookie 失效），按错误内容归并后只提示一次
    grouped = {}
    for base, err in errors:
        grouped.setdefault(err, []).append(base)

    # 用 SystemExit 让 GitHub Actions 明确失败，避免「看起来跑了但其实没签到」
    lines = ["签到出错:"]
    for err, bases in grouped.items():
        lines.append("  " + "、".join(bases) + ":")
        lines.append(err)
    raise SystemExit("\n".join(lines))


if __name__ == "__main__":
    glados_checkin()
