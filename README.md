# yugioh-ccb

游戏王猜卡小游戏。玩家根据每次猜测后的属性对比、标签提示和卡图信息，逐步猜出目标卡片。

数据来源：YGOPRO、mycard.world / ygocdb、萌卡卡图源。


## 功能

- 单人猜卡：支持热门怪兽卡、怪兽卡、魔法卡、陷阱卡、全部卡片题库。
- 多人房间：创建房间、房间号加入、准备、限时对战、排行计分。
- 联想搜索：输入卡名时自动搜索候选卡片。
- 提示机制：可获取卡片标签提示。
- 本地卡图：支持下载并从 `static/card` 读取卡图。
- 数据更新：支持更新 `cards.cdb`、热门卡标记、日文收录卡包信息和卡图。

## 本地运行

建议使用 Python 3.11。

```bash
pip install -r requirements.txt
python guess_card_game.py
```

浏览器访问：

```text
http://127.0.0.1:7860
```

如果需要使用其他端口：

```bash
PORT=5000 python guess_card_game.py
```

Windows PowerShell：

```powershell
$env:PORT="5000"
python guess_card_game.py
```

## 数据文件

项目默认从 `asset/` 读取基础数据：

```text
asset/cards.cdb
asset/cards.cdb.md5
asset/strings.conf
```

卡图默认放在：

```text
static/card/
```

也可以通过环境变量修改路径：

```bash
DATA_DIR=/path/to/asset IMAGE_DIR=/path/to/card python guess_card_game.py
```

## 更新卡片数据

更新数据库、热门卡、收录信息和卡图：

```bash
python card_build.py
```

只更新数据库和元数据，不下载卡图：

```bash
python card_build.py --skip-images
```

常用参数：

```text
--force                 忽略本地 MD5，强制重建数据库
--skip-hot              跳过热门卡标记
--skip-pack-info        跳过日文收录卡包信息
--skip-category         跳过效果标签数据更新
--image-id <id>         只下载指定卡图，可重复传入
--image-limit <n>       只下载前 n 张卡图，用于测试
--workers <n>           卡图下载并发数
--pack-workers <n>      卡包信息更新并发数
```

## Docker 部署

服务器安装 Docker 和 Docker Compose 后，在项目目录执行：

```bash
docker compose up -d --build
```

访问：

```text
http://服务器IP:7860
```

默认使用 Redis 保存 session，并挂载以下目录用于持久化：

```text
./asset       -> /app/asset
./static/card -> /app/static/card
```

首次启动时，如果挂载的 `./asset` 中没有数据文件，容器会从镜像内置数据复制 `cards.cdb`、`cards.cdb.md5` 和 `strings.conf`。

更新容器中的卡片数据和卡图：

```bash
docker compose run --rm web python card_build.py
docker compose restart web
```

只更新数据，不下载卡图：

```bash
docker compose run --rm web python card_build.py --skip-images
docker compose restart web
```

生产环境建议设置随机密钥：

```bash
SECRET_KEY="replace-with-a-long-random-string" docker compose up -d
```

## 环境变量

```text
PORT             Web 服务端口，默认 7860
DATA_DIR         数据目录，默认 ./asset
IMAGE_DIR        卡图目录，默认 ./static/card
SECRET_KEY       Flask session 密钥
REDIS_URL        Redis session 地址；未设置时使用默认 cookie session
WEB_CONCURRENCY  Gunicorn worker 数，Docker 默认 1
WEB_THREADS      Gunicorn 线程数，Docker 默认 8
WEB_TIMEOUT      Gunicorn 超时时间，Docker 默认 120
```

## 项目结构

```text
guess_card_game.py   Flask 应用入口
data_utils.py        卡片数据库读取和标签对比逻辑
card_build.py        卡片数据库、元数据和卡图更新脚本
map.py               游戏王类型、属性、种族、效果标签映射
templates/           页面模板
static/card/         本地卡图目录
asset/               cards.cdb、strings.conf 等数据文件
```

## Daily feedback sync

Online feedback is stored on the server:

```text
/home/ubuntu/yugioh-ccb-main/asset/feedback.jsonl
```

Pull feedback to the local workspace once:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_feedback.ps1
```

Local output:

```text
asset/feedback.jsonl
```

When the local feedback file changes, the old copy is archived before overwrite:

```text
asset/feedback_archive/
```

Register a Windows daily scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_feedback_sync_task.ps1 -At "09:00"
```

Check or remove the task:

```powershell
Get-ScheduledTask -TaskName YugiohCCBFeedbackSync
Unregister-ScheduledTask -TaskName YugiohCCBFeedbackSync -Confirm:$false
```

## TODO

- 为每张卡补充更多信息，例如初次发售年份、日文卡名、收录卡包等。
- 扩展题库范围，例如泛用卡、妹卡等。
- 继续完善多人对战体验。
- 改进 UI 和移动端适配。

## 支持

B 站主页：https://space.bilibili.com/178060734
