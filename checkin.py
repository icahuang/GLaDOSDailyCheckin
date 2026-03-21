import requests
import os

def glados_checkin():
    # 从 GitHub Secrets 中读取 Cookie
    cookie = os.environ.get("GLADOS_COOKIE")
    if not cookie:
        print("未找到 GLADOS_COOKIE，请检查 Secrets 配置。")
        return

    # 近期开启域名迁移/限制：老域名会返回 "please checkin via https://glados.cloud"
    # 因此这里按优先级尝试新域名 -> 老域名，避免再次迁移导致脚本失效。
    base_urls = ["https://glados.cloud", "https://glados.network"]

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

    try:
        session = requests.Session()

        last_error = None
        for base in base_urls:
            origin = base
            referer = f"{base}/console/checkin"
            url = f"{base}/api/user/checkin"
            url2 = f"{base}/api/user/status"

            # token 跟随域名，避免服务端校验失败
            payload = {'token': base.replace("https://", "")}

            headers = {
                'cookie': cookie,
                'referer': referer,
                'origin': origin,
                'user-agent': user_agent,
                'content-type': 'application/json;charset=UTF-8',
                'accept': 'application/json, text/plain, */*'
            }

            try:
                # 发送签到请求
                response = session.post(url, headers=headers, json=payload, timeout=30)
                if response.status_code != 200:
                    raise RuntimeError(f"签到接口 HTTP {response.status_code}: {response.text[:500]}")

                try:
                    checkin_data = response.json()
                except Exception:
                    raise RuntimeError(f"签到接口返回非 JSON: {response.text[:500]}")

                # GLaDOS 通常使用 code==0 表示成功；仅打印 message 容易误判
                code = checkin_data.get('code')
                message = checkin_data.get('message') or checkin_data.get('msg') or str(checkin_data)
                print(f"[{base}] 签到返回: code={code}, message={message}")
                if code != 0:
                    raise RuntimeError(f"签到失败: {checkin_data}")

                # 查询余额/天数状态
                status_res = session.get(url2, headers=headers, timeout=30)
                if status_res.status_code != 200:
                    raise RuntimeError(f"状态接口 HTTP {status_res.status_code}: {status_res.text[:500]}")
                status_data = status_res.json()
                if status_data.get('code') == 0:
                    left_days = int(float(status_data['data']['leftDays']))
                    print(f"剩余会员天数: {left_days} 天")
                else:
                    raise RuntimeError(f"状态接口返回失败: {status_data}")

                # 成功则不再尝试其它域名
                return
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"所有域名尝试失败，最后一次错误：{last_error}")
            
    except Exception as e:
        # 让 GitHub Actions 明确失败，避免“看起来跑了但其实没签到”
        # SystemExit 会让程序立即退出，且不会显示堆栈跟踪信息。
        # 这里用于在发生错误时退出脚本并返回错误信息，方便 CI/CD 工具（如 GitHub Actions）检测失败。
        raise SystemExit(f"签到出错: {e}")

if __name__ == "__main__":
    glados_checkin()