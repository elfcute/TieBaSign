# -*- coding:utf-8 -*-
import os
import requests
import hashlib
import time
import copy
import logging
import random
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from config import Config

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

ENV = os.environ

s = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=Config.HTTP_SETTINGS["POOL_CONNECTIONS"],  # 连接池的连接数
    pool_maxsize=Config.HTTP_SETTINGS["POOL_MAXSIZE"],  # 连接池的最大数量
    max_retries=Config.HTTP_SETTINGS["RETRY_TIMES"],  # 最大重试次数
    pool_block=False,  # 连接池满时不阻塞
)
s.mount("http://", adapter)
s.mount("https://", adapter)


def get_tbs(bduss):
    logger.info("获取tbs开始")
    headers = copy.copy(Config.HEADERS)
    headers.update({"Cookie": f"BDUSS={bduss}"})
    try:
        tbs = s.get(
            url=Config.API_URLS["TBS_URL"],
            headers=headers,
            timeout=Config.HTTP_SETTINGS["TIMEOUT"],
        ).json()["tbs"]
    except Exception as e:
        logger.error("获取tbs出错: %s", e)
        logger.info("重新获取tbs开始")
        tbs = s.get(
            url=Config.API_URLS["TBS_URL"],
            headers=headers,
            timeout=Config.HTTP_SETTINGS["TIMEOUT"],
        ).json()["tbs"]
    logger.info("获取tbs结束")
    return tbs


def get_favorite(bduss):
    logger.info("获取关注的贴吧开始")
    all_bars = []
    page = 1

    while True:
        data = {
            "BDUSS": bduss,
            "_client_type": "2",
            "_client_id": "wappc_1534235498291_488",
            "_client_version": "9.7.8.0",
            "_phone_imei": "000000000000000",
            "from": "1008621y",
            "page_no": str(page),
            "page_size": "200",
            "model": "MI+5",
            "net_type": "1",
            "timestamp": str(int(time.time())),
            "vcode_tag": "11",
        }

        try:
            res = s.post(
                url=Config.API_URLS["LIKE_URL"],
                data=encodeData(data),
                timeout=Config.HTTP_SETTINGS["TIMEOUT"],
            ).json()

            if not res.get("forum_list"):
                break

            for forum_type in ["non-gconforum", "gconforum"]:
                if forum_type in res["forum_list"]:
                    items = res["forum_list"][forum_type]
                    if isinstance(items, list):
                        all_bars.extend(items)
                    else:
                        all_bars.append(items)

            if res.get("has_more") != "1":
                break

            page += 1

        except Exception as e:
            logger.error(f"获取第{page}页贴吧列表失败: {e}")
            break

    logger.info(f"共获取到{len(all_bars)}个贴吧")
    return all_bars


def encodeData(data):
    s = ""
    keys = data.keys()
    for i in sorted(keys):
        s += i + "=" + str(data[i])
    sign = hashlib.md5((s + "tiebaclient!!!").encode("utf-8")).hexdigest().upper()
    data.update({"sign": str(sign)})
    return data


def client_sign(bduss, tbs, fid, kw):
    logger.info("开始签到贴吧：" + kw)
    data = copy.copy(Config.SIGN_DATA)
    data.update(
        {
            "BDUSS": bduss,
            "fid": fid,
            "kw": kw,
            "tbs": tbs,
            "timestamp": str(int(time.time())),
        }
    )
    data = encodeData(data)
    res = s.post(
        url=Config.API_URLS["SIGN_URL"],
        data=data,
        timeout=Config.HTTP_SETTINGS["TIMEOUT"],
    ).json()
    return res


def sign_one_bar(args):
    """单个贴吧签到函数"""
    bduss, tbs, bar = args
    try:
        time.sleep(
            random.randint(
                Config.THREAD_SETTINGS["MIN_DELAY"], Config.THREAD_SETTINGS["MAX_DELAY"]
            )
        )
        res = client_sign(bduss, tbs, bar["id"], bar["name"])

        error_code = res.get("error_code", "unknown")
        status = Config.ERROR_CODES.get(str(error_code), f"未知错误: {error_code}")

        if error_code in Config.SUCCESS_CODES:
            logger.info(f'贴吧：{bar["name"]} 签到状态：{status}')
        elif error_code in Config.CRITICAL_ERRORS:
            logger.warning(f'贴吧：{bar["name"]} 签到状态：{status}')
        else:
            logger.error(f'贴吧：{bar["name"]} 签到状态：{status}')

        return {
            "name": bar["name"],
            "status": status,
            "error_code": error_code,
            "is_success": error_code in Config.SUCCESS_CODES,
        }
    except Exception as e:
        logger.error(f'贴吧：{bar["name"]} 签到异常：{str(e)}')
        return {
            "name": bar["name"],
            "status": "签到异常",
            "error": str(e),
            "is_success": False,
        }


def main():
    if "BDUSS" not in ENV:
        logger.error("未配置BDUSS")
        return

    b = ENV["BDUSS"].split("#")
    max_workers = min(Config.THREAD_SETTINGS["MAX_WORKERS"], os.cpu_count() * 5)

    for n, bduss in enumerate(b):
        logger.info(f"开始签到第{n+1}个用户")
        try:
            tbs = get_tbs(bduss)
            favorites = get_favorite(bduss)
        except Exception as e:
            logger.error(f"用户签到失败: {e}")
            continue

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            sign_args = [(bduss, tbs, bar) for bar in favorites]
            futures = [executor.submit(sign_one_bar, args) for args in sign_args]

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                    if result.get("error_code") in Config.CRITICAL_ERRORS:
                        logger.warning(
                            f"检测到严重问题：{result['status']}，停止当前用户的签到"
                        )
                        executor._threads.clear()
                        break


if __name__ == "__main__":
    main()
