import asyncio
import os
import sys
import uuid

from aiohttp import ClientSession, ClientTimeout
from loguru import logger

sys.path.append(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

logger.remove()
logger.add(sys.stdout, colorize=True,
           format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> <blue> {extra[user]} </blue> <level>{message}</level>",
           backtrace=True, diagnose=True)


class BiliUser:

    def __init__(self, access_token: str, refresh_token: str, white_uids: str = "", banned_uids: str = "",
                 config: dict = None):
        if config is None:
            config: dict = {}
        from .api import BiliApi
        self.mid, self.name = 0, ""
        self.access_key: str = access_token  # 登录凭证
        self.refresh_key: str = refresh_token
        try:
            self.white_list = list(map(lambda x: int(x if x else 0), str(white_uids).split(",")))  # 白名单UID
            self.banned_list = list(map(lambda x: int(x if x else 0), str(banned_uids).split(",")))  # 黑名单
        except ValueError:
            raise ValueError("白名单或黑名单格式错误")
        self.config: dict = config
        self.medals: list = []  # 用户所有勋章
        self.worn_medal: dict = {}  # 用户正在佩戴的勋章
        self.medals_lower_20: list = []  # 用户所有勋章，等级小于20的

        self.session: ClientSession = ClientSession(timeout=ClientTimeout(total=3))
        self.api: BiliApi = BiliApi(self, self.session)

        self.retry_times: int = 0  # 点赞任务重试次数
        self.max_retry_times: int = 10  # 最大重试次数
        self.message: list = []
        self.errmsg: list = ["错误日志："]
        self.uuids: list = [str(uuid.uuid4()) for _ in range(2)]
        self.log = logger.bind(user="B站粉丝牌助手")
        self.is_login: bool = False

    async def login_verify(self) -> bool:
        """
        登录验证
        """
        import time
        check_info = await self.api.check_token()
        expired = time.strftime("%Y年%m月%d日 %H:%M:%S", time.localtime((time.time() + check_info["expires_in"])))
        self.log.log("INFO", "令牌有效期至: {expired}".format(expired=expired))
        if check_info["expires_in"] < 14400:
            from ruamel.yaml import YAML
            yaml = YAML()
            self.log.log("WARNING", "令牌即将过期, 正在申请更换...")
            resp = await self.api.refresh_token()
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "users.yaml"), "r",
                      encoding="utf-8") as f:
                users = yaml.load(f.read())
                f.close()
            for user in users["USERS"]:
                if check_info["access_token"] == user["access_key"]:
                    user["access_key"] = self.access_key = resp["token_info"]["access_token"]
                    user["refresh_key"] = self.refresh_key = resp["token_info"]["refresh_token"]
            with open(os.path.join(os.path.realpath(os.path.dirname(__file__)), "..", "users.yaml"), "w",
                      encoding="utf-8") as w:
                yaml.dump(users, w)
                w.close()
            self.log.log("SUCCESS", "令牌更换成功")
        else:
            self.log.log("INFO", "令牌状态正常")
        login_info = await self.api.login_verify()
        self.mid, self.name = login_info["mid"], login_info["name"]
        self.log = logger.bind(user=self.name)
        if login_info["mid"] == 0:
            return False
        self.log.log("SUCCESS", str(login_info["mid"]) + " 登录成功")
        self.is_login = True
        return True

    async def do_sign(self):
        try:
            sign_info = await self.api.do_sign()
            self.log.log("SUCCESS", "签到成功,本月签到次数: {}/{}".format(sign_info["hadSignDays"], sign_info["allDays"]))
            self.message.append(f"【{self.name}】 签到成功,本月签到次数: {sign_info['hadSignDays']}/{sign_info['allDays']}")
        except Exception as e:
            self.log.log("ERROR", e)
            self.errmsg.append(f"【{self.name}】" + str(e))
        user_info = await self.api.get_user_info()
        self.log.log("INFO",
                     "当前用户UL等级: {} ,还差 {} 经验升级".format(user_info["exp"]["user_level"], user_info["exp"]["unext"]))
        self.message.append(
            f"【{self.name}】 UL等级: {user_info['exp']['user_level']} ,还差 {user_info['exp']['unext']} 经验升级")

    async def get_medals(self):
        """
        获取用户勋章
        """
        self.medals.clear()
        async for medal in self.api.get_fans_medal_and_room_id():
            if self.white_list == [0]:
                if medal["medal"]["target_id"] in self.banned_list:
                    self.log.warning(f"{medal['anchor_info']['nick_name']} 在黑名单中，已过滤")
                    continue
                self.medals.append(medal) if medal["room_info"]["room_id"] != 0 else ...
            else:
                if medal["medal"]["target_id"] in self.white_list:
                    self.medals.append(medal) if medal["room_info"]["room_id"] != 0 else ...
                    self.log.success(f"{medal['anchor_info']['nick_name']} 在白名单中，加入任务")
        self.medals_lower_20.clear()
        [self.medals_lower_20.append(medal) for medal in self.medals if medal["medal"]["level"] < 20]

    async def async_like_and_share(self, failed_medals=None):
        """
        点赞, 分享
        """
        if failed_medals is None:
            failed_medals = []
        if self.config["LIKE_CD"] == 0:
            self.log.log("INFO", "点赞任务已关闭")
        elif self.config["SHARE_CD"] == 0:
            self.log.log("INFO", "分享任务已关闭")
        if self.config["LIKE_CD"] == 0 and self.config["SHARE_CD"] == 0:
            return
        try:
            if not failed_medals:
                failed_medals = self.medals_lower_20
            if not self.config["ASYNC"]:
                self.log.log("INFO", "同步点赞、分享任务开始....")
                for index, medal in enumerate(failed_medals):
                    tasks = []
                    tasks.append(self.api.like_interact(medal["room_info"]
                                                        ["room_id"])) if self.config["LIKE_CD"] else ...
                    tasks.append(self.api.share_room(medal["room_info"]["room_id"])) if self.config["SHARE_CD"] else ...
                    await asyncio.gather(*tasks)
                    self.log.log(
                        "SUCCESS",
                        f"{medal['anchor_info']['nick_name']} 点赞,分享成功 {index + 1}/{len(self.medals_lower_20)}")
                    await asyncio.sleep(max(self.config["LIKE_CD"], self.config["SHARE_CD"]))
            else:
                self.log.log("INFO", "异步点赞、分享任务开始....")
                all_tasks = []
                for medal in failed_medals:
                    all_tasks.append(self.api.like_interact(medal["room_info"]
                                                            ["room_id"])) if self.config["LIKE_CD"] else ...
                    all_tasks.append(self.api.share_room(medal["room_info"]
                                                         ["room_id"])) if self.config["SHARE_CD"] else ...
                await asyncio.gather(*all_tasks)
            await asyncio.sleep(10)
            await self.get_medals()  # 刷新勋章
            self.log.log("SUCCESS", "点赞、分享任务完成")
            finally_medals = [medal for medal in self.medals_lower_20 if medal["medal"]["today_feed"] >= 200]
            failed_medals = [medal for medal in self.medals_lower_20 if medal["medal"]["today_feed"] < 200]
            msg = "20级以下牌子共 {} 个,完成任务 {} 个亲密度大于等于200".format(
                len(self.medals_lower_20), len(finally_medals))
            self.log.log("INFO", msg)
            self.log.log("WARNING", "小于200或失败房间: {}... {}个".format(
                " ".join([medals["anchor_info"]["nick_name"] for medals in failed_medals[:5]]), len(failed_medals)))
            if self.retry_times > self.max_retry_times:
                self.log.log("ERROR", "任务重试次数过多,停止任务")
                return
            if len(finally_medals) / len(self.medals_lower_20) <= 0.9:
                self.log.log("WARNING", "成功率过低,重新执行任务")
                self.retry_times += 1
                self.log.log("WARNING", "重试次数: {}/{}".format(self.retry_times, self.max_retry_times))
                await self.async_like_and_share(failed_medals)
        except Exception:
            self.log.exception("点赞、分享任务异常")
            self.errmsg.append(f"【{self.name}】 点赞、分享任务异常,请检查日志")

    async def send_danmaku(self):
        """
        每日弹幕打卡
        """
        if not self.config["DANMAKU_CD"]:
            self.log.log("INFO", "弹幕任务关闭")
            return
        # 缓存粉丝勋章
        import copy
        medals = copy.deepcopy(self.medals_lower_20)  # 弹幕打卡20级以下直播间
        worn_medal = copy.deepcopy(self.worn_medal) if self.worn_medal else {}
        self.log.log("INFO", "弹幕打卡任务开始....(预计 {} 秒完成)".format(len(medals) * self.config["DANMAKU_CD"]))
        n = 0
        for medal in medals:
            try:
                (await self.api.wear_medal(medal["medal"]["medal_id"])) if self.config["WEARMEDAL"] else ...
                danmaku = await self.api.send_danmaku(medal["room_info"]["room_id"])
                n += 1
                self.log.log(
                    "DEBUG", "{} 房间弹幕打卡成功: {} ({}/{})".format(medal["anchor_info"]["nick_name"], danmaku, n,
                                                              len(medals)))
            except Exception as e:
                self.log.log("ERROR", "{} 房间弹幕打卡失败: {}".format(medal["anchor_info"]["nick_name"], e))
                self.errmsg.append(f"【{self.name}】 {medal['anchor_info']['nick_name']} 房间弹幕打卡失败: {str(e)}")
            finally:
                await asyncio.sleep(self.config["DANMAKU_CD"])
        (await self.api.wear_medal(worn_medal["medal"]["medal_id"])) if self.config[
                                                                            "WEARMEDAL"] and worn_medal else ...
        self.log.log("SUCCESS", "弹幕打卡任务完成")
        self.message.append(f"【{self.name}】 弹幕打卡任务完成 {n}/{len(medals)}")

    async def init(self):
        if not await self.login_verify():
            self.log.log("ERROR", "登录失败")
            self.errmsg.append("登录失败")
            await self.session.close()
        else:
            await self.do_sign()
            await self.get_medals()

    async def start(self):
        if self.is_login:
            task = [self.async_like_and_share(), self.send_danmaku(), self.watching_live(), self.sign_in_groups()]
            await asyncio.gather(*task)
        # await self.session.close()

    async def send_msg(self):
        if not self.is_login:
            await self.session.close()
            return self.message + self.errmsg
        await self.get_medals()
        name_list1, name_list2, name_list3, name_list4 = [], [], [], []
        for medal in self.medals_lower_20:
            today_feed = medal["medal"]["today_feed"]
            nick_name = medal["anchor_info"]["nick_name"]
            if today_feed >= 1500:
                name_list1.append(nick_name)
            elif 1300 < today_feed <= 1400:
                name_list2.append(nick_name)
            elif 1200 < today_feed <= 1300:
                name_list3.append(nick_name)
            elif today_feed <= 1200:
                name_list4.append(nick_name)
        self.message.append(f"【{self.name}】 今日亲密度获取情况如下（20级以下）：")

        for length, name in zip([name_list1, name_list2, name_list3, name_list4],
                                ["【1500】", "【1300至1400】", "【1200至1300】", "【1200以下】"]):
            if len(length) > 0:
                self.message.append(
                    f"{name}" + " ".join(length[:5]) + f"{' 等' if len(length) > 5 else ''}" + f" {len(length)}个")
        await self.session.close()
        return self.message + self.errmsg + ["---"]

    async def watching_live(self):
        if not self.config["WATCHINGLIVE"]:
            self.log.log("INFO", "每日观看直播任务关闭")
            return
        heart_max = self.config['WATCHINGLIVE']
        self.log.log("INFO", f"每日{heart_max}分钟任务开始")
        heart_num = 0
        while True:
            tasks = []
            for medal in self.medals_lower_20:
                # 只观看20级以下的直播间
                tasks.append(self.api.heartbeat(medal["room_info"]["room_id"], medal["medal"]["target_id"]))
            await asyncio.gather(*tasks)
            heart_num += 1
            self.log.log(
                "INFO",
                f"{' '.join([medal['anchor_info']['nick_name'] for medal in self.medals_lower_20[:5]])} 等共 {len(self.medals_lower_20)} 个房间的第{heart_num}次心跳包已发送（{heart_num}/{heart_max}）")
            await asyncio.sleep(60)
            if heart_num >= heart_max:
                break
        self.log.log("SUCCESS", f"每日{heart_max}分钟任务完成")

    async def sign_in_groups(self):
        if not self.config["SIGNINGROUP"]:
            self.log.log("INFO", "应援团签到任务关闭")
            return
        self.log.log("INFO", "应援团签到任务开始")
        try:
            n = 0
            async for group in self.api.get_groups():
                if group["owner_uid"] == self.mid:
                    continue
                try:
                    await self.api.sign_in_groups(group["group_id"], group["owner_uid"])
                except Exception as e:
                    self.log.log("ERROR", group["group_name"] + " 签到失败")
                    self.errmsg.append(f"应援团签到失败: {e}")
                    continue
                self.log.log("DEBUG", group["group_name"] + " 签到成功")
                await asyncio.sleep(self.config["SIGNINGROUP"])
                n += 1
            if n:
                self.log.log("SUCCESS", f"应援团签到任务完成 {n}/{n}")
                self.message.append(f" 应援团签到任务完成 {n}/{n}")
            else:
                self.log.log("WARNING", "没有加入应援团")
        except Exception as e:
            self.log.exception(e)
            self.log.log("ERROR", "应援团签到任务失败: " + str(e))
            self.errmsg.append("应援团签到任务失败: " + str(e))
