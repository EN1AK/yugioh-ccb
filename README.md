## yugioh-ccb

游戏王笑传之猜抽棒

![image](https://github.com/user-attachments/assets/75092bdb-71b0-4f9a-9b92-ebbb75654ea5)

数据来源：ygopro,mycard.world

### 玩法

服务已停止，网页无法访问

~1、浏览器打开 111.229.8.218~

~2、选择题库（目前仅推荐热门怪兽卡，其他太难）~

~3、第二、五次猜测会给出提示~


## 部署指南

   ```
   pip install -r requirements.txt
   python guess_card_game.py 
   ```

浏览器打开http://127.0.0.1:5000

## Todo list：

·为每张卡添加更多信息（初次发售年份，nw/md/简中/日文卡名，收录卡包等）

·题库范围（泛用卡/妹卡等）

·多人对战

·卡图显示√

·UI改进

## 支持

b站首页：https://space.bilibili.com/178060734



## Docker 部署

服务器安装 Docker 和 Docker Compose 后，在项目目录执行：

```bash
docker compose up -d --build
```

访问：

```text
http://服务器IP:7860
```

默认使用 Redis 保存 session，并将以下目录挂载到容器内，便于持久化更新：

```text
./asset -> /app/asset
./static/card -> /app/static/card
```

`asset/` 内保存 `cards.cdb`、`cards.cdb.md5`、`strings.conf`。首次启动时，如果挂载的 `./asset` 为空，容器会自动复制镜像内置数据到 `./asset`。

更新卡片数据和卡图：

```bash
docker compose run --rm web python card_build.py
docker compose restart web
```

如果只想先更新数据库、不下载卡图：

```bash
docker compose run --rm web python card_build.py --skip-images
docker compose restart web
```

生产环境建议设置随机密钥：

```bash
SECRET_KEY="换成一串随机长字符串" docker compose up -d
```
