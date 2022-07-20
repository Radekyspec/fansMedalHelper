import asyncio
import itertools
import os
import warnings

import aiohttp
from loguru import logger

from src import BiliUser

log = logger.bind(user="B站粉丝牌助手")
__VERSION__ = "0.3.5"

warnings.filterwarnings(
    "ignore",
    message="The localize method is no longer necessary, as this time zone supports the fold attribute",
)
os.chdir(os.path.dirname(os.path.abspath(__file__)).split(__file__)[0])
try:
    if os.environ.get("USERS"):
        import json
        users = json.loads(os.environ.get("USERS"))
    else:
        import yaml
        with open("users.yaml", "r", encoding="utf-8") as f:
            users = yaml.load(f, Loader=yaml.FullLoader)
            f.close()
    assert users["ASYNC"] in [0, 1], "ASYNC参数错误"
    assert users["LIKE_CD"] >= 0, "LIKE_CD参数错误"
    assert users["SHARE_CD"] >= 0, "SHARE_CD参数错误"
    assert users["DANMAKU_CD"] >= 0, "DANMAKU_CD参数错误"
    assert users["WATCHINGLIVE"] >= 0, "WATCHINGLIVE参数错误"
    assert users["WEARMEDAL"] in [0, 1], "WEARMEDAL参数错误"
    assert users["SIGNINGROUP"] >= 0, "SIGNINGROUP参数错误"
    from distutils.util import strtobool
    config = {
        "ASYNC": bool(strtobool(str(users["ASYNC"]))),
        "LIKE_CD": users["LIKE_CD"],
        "SHARE_CD": users["SHARE_CD"],
        "DANMAKU_CD": users["DANMAKU_CD"],
        "WATCHINGLIVE": users["WATCHINGLIVE"],
        "WEARMEDAL": bool(strtobool(str(users["WEARMEDAL"]))),
        "SIGNINGROUP": users["SIGNINGROUP"],
        "PROXY": users.get("PROXY"),
    }
except Exception as e:
    users = {}
    log.error(f"读取配置文件失败,请检查配置文件格式是否正确: {e}")
    exit(1)


@log.catch
async def main():
    message_list = []
    session = aiohttp.ClientSession()
    try:
        log.warning("当前版本为: " + __VERSION__)
        resp = await (
            await session.get("http://version.fansmedalhelper.1961584514352337.cn-hangzhou.fc.devsapp.net/")).json()
        if resp["version"] != __VERSION__:
            log.warning("新版本为: " + resp["version"] + ",请更新")
            log.warning("更新内容: " + resp["changelog"])
            message_list.append(f"当前版本: {__VERSION__} ,最新版本: {resp['version']}")
            message_list.append(f"更新内容: {resp['changelog']} ")
    except Exception:
        message_list.append("检查版本失败")
        log.warning("检查版本失败")
    init_tasks = []
    start_tasks = []
    catch_msg = []
    for user in users["USERS"]:
        if user["access_key"] and user["refresh_key"]:
            bili_user = BiliUser(user["access_key"], user["refresh_key"], user.get("white_uid", ""),
                                 user.get("banned_uid", ""), config)
            init_tasks.append(bili_user.init())
            start_tasks.append(bili_user.start())
            catch_msg.append(bili_user.send_msg())
    try:
        await asyncio.gather(*init_tasks)
        await asyncio.gather(*start_tasks)
        message_list = message_list + list(itertools.chain.from_iterable(await asyncio.gather(*catch_msg)))
    except Exception as ex:
        log.exception(ex)
        message_list.append(f"任务执行失败: {ex}")
    [log.info(message) for message in message_list]
    if users.get("SENDKEY", ""):
        await push_message(session, users["SENDKEY"], "  \n".join(message_list))
    await session.close()
    if users.get("MOREPUSH", ""):
        from onepush import notify
        notifier = users["MOREPUSH"]["notifier"]
        params = users["MOREPUSH"]["params"]
        await notify(notifier, title=f"【B站粉丝牌助手推送】", content="  \n".join(message_list), **params,
                     proxy=config.get("PROXY"))
        log.info(f"{notifier} 已推送")


def run():
    new_loop = asyncio.new_event_loop()
    new_loop.run_until_complete(main())
    log.info("任务结束,等待下一次执行")


async def push_message(session, sendkey, message):
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = {"title": f"【B站粉丝牌助手推送】", "desp": message}
    await session.post(url, data=data)
    log.info("Server酱已推送")


if __name__ == "__main__":
    cron = users.get("CRON", None)
    if cron:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
        log.info("使用内置定时器,开启定时任务,等待时间到达后执行")
        schedulers = BlockingScheduler()
        schedulers.add_job(
            run,
            CronTrigger.from_crontab(cron),
            misfire_grace_time=3600,
        )
        try:
            schedulers.start()
        except KeyboardInterrupt:
            pass
    else:
        log.info("外部调用,开启任务")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
        log.info("任务结束")
