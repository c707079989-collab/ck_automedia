import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from queue import Queue
from flask_cors import CORS
from myUtils.auth import check_cookie
from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from werkzeug.utils import secure_filename
from conf import BASE_DIR
from myUtils.login import get_tencent_cookie, douyin_cookie_gen, get_ks_cookie, xiaohongshu_cookie_gen
from myUtils.postVideo import post_video_tencent, post_video_DouYin, post_video_ks, post_video_xhs
# 本地补丁：B站走 sau_commands.bilibili（biliup 子进程），不改动上游 uploader 调用方式
from sau_commands.bilibili import (
    upload_bilibili_video,
    BilibiliVideoUploadRequest,
    check_bilibili_account,
)
from utils.files_times import generate_schedule_time_next_day

active_queues = {}
app = Flask(__name__)

#允许所有来源跨域访问
CORS(app)

# 限制上传文件大小为160MB
app.config['MAX_CONTENT_LENGTH'] = 160 * 1024 * 1024

# 获取当前目录（假设 index.html 和 assets 在这里）
current_dir = os.path.dirname(os.path.abspath(__file__))

# 处理所有静态资源请求（未来打包用）
@app.route('/assets/<filename>')
def custom_static(filename):
    return send_from_directory(os.path.join(current_dir, 'assets'), filename)

# 处理 favicon.ico 静态资源（未来打包用）
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(current_dir, 'assets'), 'vite.svg')

@app.route('/vite.svg')
def vite_svg():
    return send_from_directory(os.path.join(current_dir, 'assets'), 'vite.svg')

# （未来打包用）
@app.route('/')
def index():  # put application's code here
    return send_from_directory(current_dir, 'index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400
    try:
        # 保存文件到指定位置
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        safe_name = secure_filename(file.filename)
        if not safe_name:
            return jsonify({"code": 400, "data": None, "msg": "Invalid filename"}), 400
        filepath = Path(BASE_DIR / "videoFile" / f"{uuid_v1}_{safe_name}")
        file.save(filepath)
        return jsonify({"code":200,"msg": "File uploaded successfully", "data": f"{uuid_v1}_{safe_name}"}), 200
    except Exception as e:
        return jsonify({"code":500,"msg": str(e),"data":None}), 500

@app.route('/getFile', methods=['GET'])
def get_file():
    # 获取 filename 参数
    filename = request.args.get('filename')

    if not filename:
        return jsonify({"code": 400, "msg": "filename is required", "data": None}), 400

    # 防止路径穿越攻击
    if '..' in filename or filename.startswith('/'):
        return jsonify({"code": 400, "msg": "Invalid filename", "data": None}), 400

    # 拼接完整路径
    file_path = str(Path(BASE_DIR / "videoFile"))

    # 返回文件
    return send_from_directory(file_path,filename)


@app.route('/uploadSave', methods=['POST'])
def upload_save():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400

    # 获取表单中的自定义文件名（可选）
    custom_filename = request.form.get('filename', None)
    if custom_filename:
        filename = secure_filename(custom_filename + "." + file.filename.split('.')[-1])
    else:
        filename = secure_filename(file.filename)
    if not filename:
        return jsonify({"code": 400, "data": None, "msg": "Invalid filename"}), 400

    try:
        # 生成 UUID v1
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")

        # 构造文件名和路径
        final_filename = f"{uuid_v1}_{filename}"
        filepath = Path(BASE_DIR / "videoFile" / f"{uuid_v1}_{filename}")

        # 保存文件
        file.save(filepath)

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO file_records (filename, filesize, file_path)
            VALUES (?, ?, ?)
                                ''', (filename, round(float(os.path.getsize(filepath)) / (1024 * 1024),2), final_filename))
            conn.commit()
            print("✅ 上传文件已记录")

        return jsonify({
            "code": 200,
            "msg": "File uploaded and saved successfully",
            "data": {
                "filename": filename,
                "filepath": final_filename
            }
        }), 200

    except Exception as e:
        print(f"Upload failed: {e}")
        return jsonify({
            "code": 500,
            "msg": f"upload failed: {e}",
            "data": None
        }), 500

@app.route('/getFiles', methods=['GET'])
def get_all_files():
    try:
        # 使用 with 自动管理数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row  # 允许通过列名访问结果
            cursor = conn.cursor()

            # 查询所有记录
            cursor.execute("SELECT * FROM file_records")
            rows = cursor.fetchall()

            # 将结果转为字典列表，并提取UUID
            data = []
            for row in rows:
                row_dict = dict(row)
                # 从 file_path 中提取 UUID (文件名的第一部分，下划线前)
                if row_dict.get('file_path'):
                    file_path_parts = row_dict['file_path'].split('_', 1)  # 只分割第一个下划线
                    if len(file_path_parts) > 0:
                        row_dict['uuid'] = file_path_parts[0]  # UUID 部分
                    else:
                        row_dict['uuid'] = ''
                else:
                    row_dict['uuid'] = ''
                data.append(row_dict)

            return jsonify({
                "code": 200,
                "msg": "success",
                "data": data
            }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("get file failed!"),
            "data": None
        }), 500


@app.route("/getAccounts", methods=['GET'])
def getAccounts():
    """快速获取所有账号信息，不进行cookie验证"""
    try:
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
            SELECT * FROM user_info''')
            rows = cursor.fetchall()
            rows_list = [list(row) for row in rows]

            print("\n📋 当前数据表内容（快速获取）：")
            for row in rows:
                print(row)

            return jsonify(
                {
                    "code": 200,
                    "msg": None,
                    "data": rows_list
                }), 200
    except Exception as e:
        print(f"获取账号列表时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"获取账号列表失败: {str(e)}",
            "data": None
        }), 500


@app.route("/getValidAccounts",methods=['GET'])
async def getValidAccounts():
    with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM user_info''')
        rows = cursor.fetchall()
        rows_list = [list(row) for row in rows]
        print("\n📋 当前数据表内容：")
        for row in rows:
            print(row)
        for row in rows_list:
            # 本地补丁：B站（type=5）走 sau bilibili check，其余走原 cookie 校验
            if row[1] == 5:
                flag = await check_bilibili_account(row[2])
            else:
                flag = await check_cookie(row[1], row[2])
            if not flag:
                row[4] = 0
                cursor.execute('''
                UPDATE user_info 
                SET status = ? 
                WHERE id = ?
                ''', (0,row[0]))
                conn.commit()
                print("✅ 用户状态已更新")
        for row in rows:
            print(row)
        return jsonify(
                        {
                            "code": 200,
                            "msg": None,
                            "data": rows_list
                        }),200

@app.route('/deleteFile', methods=['GET'])
def delete_file():
    file_id = request.args.get('id')

    if not file_id or not file_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing file ID",
            "data": None
        }), 400

    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM file_records WHERE id = ?", (file_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "File not found",
                    "data": None
                }), 404

            record = dict(record)

            # 获取文件路径并删除实际文件
            file_path = Path(BASE_DIR / "videoFile" / record['file_path'])
            if file_path.exists():
                try:
                    file_path.unlink()  # 删除文件
                    print(f"✅ 实际文件已删除: {file_path}")
                except Exception as e:
                    print(f"⚠️ 删除实际文件失败: {e}")
                    # 即使删除文件失败，也要继续删除数据库记录，避免数据不一致
            else:
                print(f"⚠️ 实际文件不存在: {file_path}")

            # 删除数据库记录
            cursor.execute("DELETE FROM file_records WHERE id = ?", (file_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "File deleted successfully",
            "data": {
                "id": record['id'],
                "filename": record['filename']
            }
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("delete failed!"),
            "data": None
        }), 500

@app.route('/deleteAccount', methods=['GET'])
def delete_account():
    account_id = request.args.get('id')

    if not account_id or not account_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing account ID",
            "data": None
        }), 400

    account_id = int(account_id)

    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM user_info WHERE id = ?", (account_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "account not found",
                    "data": None
                }), 404

            record = dict(record)

            # 删除关联的cookie文件
            if record.get('filePath'):
                cookie_file_path = Path(BASE_DIR / "cookiesFile" / record['filePath'])
                if cookie_file_path.exists():
                    try:
                        cookie_file_path.unlink()
                        print(f"✅ Cookie文件已删除: {cookie_file_path}")
                    except Exception as e:
                        print(f"⚠️ 删除Cookie文件失败: {e}")

            # 删除数据库记录
            cursor.execute("DELETE FROM user_info WHERE id = ?", (account_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account deleted successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"delete failed: {str(e)}",
            "data": None
        }), 500


# SSE 登录接口
@app.route('/login')
def login():
    # 1 小红书 2 视频号 3 抖音 4 快手
    type = request.args.get('type')
    # 账号名
    id = request.args.get('id')

    # 模拟一个用于异步通信的队列
    status_queue = Queue()
    active_queues[id] = status_queue

    def on_close():
        print(f"清理队列: {id}")
        del active_queues[id]
    # 启动异步任务线程
    thread = threading.Thread(target=run_async_function, args=(type,id,status_queue), daemon=True)
    thread.start()
    response = Response(sse_stream(status_queue,), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # 关键：禁用 Nginx 缓冲
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Connection'] = 'keep-alive'
    return response


# 本地补丁：B站发布（type=5）。账号身份为账号名称（sau bilibili --account <name>），
# 由 resolve_account_file 解析为 cookies/bilibili_<name>.json。tid 为 B站分区 ID，
# desc 为视频简介。发布经 biliup 子进程完成（首次会按需下载 biliup 二进制）。
def post_video_bilibili(title, files, account_names, tags, category=None, tid=0, desc='',
                        enableTimer=False, videos_per_day=1, daily_times=None, start_days=0):
    files = [Path(BASE_DIR / "videoFile" / file) for file in files]
    if isinstance(tags, list):
        tags = ",".join([t for t in tags if t])
    if category == 0:
        category = None
    # B站分区：优先用 tid，其次 category（兼容前端复用 category 字段），默认 0
    bili_tid = int(tid if tid else (category or 0))
    if enableTimer:
        publish_datetimes = generate_schedule_time_next_day(len(files), videos_per_day, daily_times, start_days)
    else:
        publish_datetimes = [0 for _ in range(len(files))]
    for index, file in enumerate(files):
        publish_date = publish_datetimes[index] if index < len(publish_datetimes) else 0
        for account_name in account_names:
            print(f"[B站] 账号={account_name} 文件={file} 标题={title} tid={bili_tid}")
            request_obj = BilibiliVideoUploadRequest(
                account_name=account_name,
                video_file=file,
                title=title,
                description=desc or title,
                tid=bili_tid,
                tags=[t for t in (tags or "").split(",") if t],
                publish_date=publish_date,
            )
            asyncio.run(upload_bilibili_video(request_obj))


@app.route('/postVideo', methods=['POST'])
def postVideo():
    # 获取JSON数据
    data = request.get_json()

    if not data:
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    # 从JSON数据中提取fileList和accountList
    file_list = data.get('fileList', [])
    account_list = data.get('accountList', [])
    type = data.get('type')
    title = data.get('title')
    tags = data.get('tags')
    category = data.get('category')
    enableTimer = data.get('enableTimer')
    if category == 0:
        category = None
    productLink = data.get('productLink', '')
    productTitle = data.get('productTitle', '')
    thumbnail_path = data.get('thumbnail', '')
    is_draft = data.get('isDraft', False)  # 新增参数：是否保存为草稿
    tid = data.get('tid', 0)  # B站分区 ID（type=5 使用）
    desc = data.get('desc', '')  # B站视频简介（type=5 使用）

    videos_per_day = data.get('videosPerDay')
    daily_times = data.get('dailyTimes')
    start_days = data.get('startDays')

    # 参数校验
    if not file_list:
        return jsonify({"code": 400, "msg": "文件列表不能为空", "data": None}), 400
    if not account_list:
        return jsonify({"code": 400, "msg": "账号列表不能为空", "data": None}), 400
    if not type:
        return jsonify({"code": 400, "msg": "平台类型不能为空", "data": None}), 400
    if not title:
        return jsonify({"code": 400, "msg": "标题不能为空", "data": None}), 400

    # 打印获取到的数据（仅作为示例）
    print("File List:", file_list)
    print("Account List:", account_list)

    try:
        match type:
            case 1:
                post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days)
            case 2:
                post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, is_draft)
            case 3:
                post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, thumbnail_path, productLink, productTitle)
            case 4:
                post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days)
            case 5:
                # 本地补丁：B站。account_list 为 B站账号名称列表
                post_video_bilibili(title, file_list, account_list, tags, category, tid, desc, enableTimer,
                                   videos_per_day, daily_times, start_days)
            case _:
                return jsonify({"code": 400, "msg": f"不支持的平台类型: {type}", "data": None}), 400

        # 返回响应给客户端
        return jsonify(
            {
                "code": 200,
                "msg": "发布任务已提交",
                "data": None
            }), 200
    except Exception as e:
        print(f"发布视频时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"发布失败: {str(e)}",
            "data": None
        }), 500


@app.route('/updateUserinfo', methods=['POST'])
def updateUserinfo():
    # 获取JSON数据
    data = request.get_json()

    # 从JSON数据中提取 type 和 userName
    user_id = data.get('id')
    type = data.get('type')
    userName = data.get('userName')
    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 更新数据库记录
            cursor.execute('''
                           UPDATE user_info
                           SET type     = ?,
                               userName = ?
                           WHERE id = ?;
                           ''', (type, userName, user_id))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account update successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("update failed!"),
            "data": None
        }), 500

@app.route('/postVideoBatch', methods=['POST'])
def postVideoBatch():
    data_list = request.get_json()

    if not isinstance(data_list, list):
        return jsonify({"code": 400, "msg": "Expected a JSON array", "data": None}), 400
    for data in data_list:
        # 从JSON数据中提取fileList和accountList
        file_list = data.get('fileList', [])
        account_list = data.get('accountList', [])
        type = data.get('type')
        title = data.get('title')
        tags = data.get('tags')
        category = data.get('category')
        enableTimer = data.get('enableTimer')
        if category == 0:
            category = None
        productLink = data.get('productLink', '')
        productTitle = data.get('productTitle', '')
        is_draft = data.get('isDraft', False)
        tid = data.get('tid', 0)  # B站分区 ID
        desc = data.get('desc', '')  # B站视频简介

        videos_per_day = data.get('videosPerDay')
        daily_times = data.get('dailyTimes')
        start_days = data.get('startDays')
        # 打印获取到的数据（仅作为示例）
        print("File List:", file_list)
        print("Account List:", account_list)
        match type:
            case 1:
                post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                               start_days)
            case 2:
                post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, is_draft)
            case 3:
                post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, productLink, productTitle)
            case 4:
                post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days)
            case 5:
                # 本地补丁：B站
                post_video_bilibili(title, file_list, account_list, tags, category, tid, desc, enableTimer,
                                   videos_per_day, daily_times, start_days)
    # 返回响应给客户端
    return jsonify(
        {
            "code": 200,
            "msg": None,
            "data": None
        }), 200

# 本地补丁：B站账号注册（仅写入数据库记录；真实登录需在本地终端执行
# `sau bilibili login --account <name>`，生成 cookies/bilibili_<name>.json）
@app.route('/bilibili/register', methods=['POST'])
def bilibili_register():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"code": 400, "msg": "账号名称不能为空", "data": None}), 400
    try:
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # 同名 B站账号覆盖，避免重复
            cursor.execute('SELECT id FROM user_info WHERE type = ? AND userName = ?', (5, name))
            existing = cursor.fetchone()
            if existing:
                cursor.execute('UPDATE user_info SET status = 0 WHERE id = ?', (existing['id'],))
                conn.commit()
                return jsonify({"code": 200, "msg": "B站账号已更新，请到终端登录", "data": {"id": existing['id']}}), 200
            cursor.execute('''
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
            ''', (5, name, name, 0))
            conn.commit()
            new_id = cursor.lastrowid
        return jsonify({
            "code": 200,
            "msg": "B站账号已登记，请在本地终端执行登录命令",
            "data": {"id": new_id, "command": f"sau bilibili login --account {name}"}
        }), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"登记失败: {str(e)}", "data": None}), 500


# 本地补丁：校验 B站登录态并更新数据库状态
@app.route('/bilibili/check', methods=['POST'])
def bilibili_check():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"code": 400, "msg": "账号名称不能为空", "data": None}), 400
    try:
        valid = asyncio.run(check_bilibili_account(name))
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE user_info SET status = ? WHERE type = ? AND userName = ?',
                          (1 if valid else 0, 5, name))
            conn.commit()
        return jsonify({
            "code": 200,
            "msg": "有效" if valid else "登录态已失效，请重新在终端登录",
            "data": {"valid": valid}
        }), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"校验失败: {str(e)}", "data": None}), 500


# Cookie文件上传API
@app.route('/uploadCookie', methods=['POST'])
def upload_cookie():
    try:
        if 'file' not in request.files:
            return jsonify({
                "code": 400,
                "msg": "没有找到Cookie文件",
                "data": None
            }), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({
                "code": 400,
                "msg": "Cookie文件名不能为空",
                "data": None
            }), 400

        if not file.filename.endswith('.json'):
            return jsonify({
                "code": 400,
                "msg": "Cookie文件必须是JSON格式",
                "data": None
            }), 400

        # 获取账号信息
        account_id = request.form.get('id')
        platform = request.form.get('platform')

        if not account_id or not platform:
            return jsonify({
                "code": 400,
                "msg": "缺少账号ID或平台信息",
                "data": None
            }), 400

        # 从数据库获取账号的文件路径
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT filePath FROM user_info WHERE id = ?', (account_id,))
            result = cursor.fetchone()

        if not result:
            return jsonify({
                "code": 500,
                "msg": "账号不存在",
                "data": None
            }), 404

        # 保存上传的Cookie文件到对应路径
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / result['filePath'])
        cookie_file_path.parent.mkdir(parents=True, exist_ok=True)

        file.save(str(cookie_file_path))

        # 更新数据库中的账号信息（可选，比如更新更新时间）
        # 这里可以根据需要添加额外的处理逻辑

        return jsonify({
            "code": 200,
            "msg": "Cookie文件上传成功",
            "data": None
        }), 200

    except Exception as e:
        print(f"上传Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"上传Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# Cookie文件下载API
@app.route('/downloadCookie', methods=['GET'])
def download_cookie():
    try:
        file_path = request.args.get('filePath')
        if not file_path:
            return jsonify({
                "code": 500,
                "msg": "缺少文件路径参数",
                "data": None
            }), 400

        # 验证文件路径的安全性，防止路径遍历攻击
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / file_path).resolve()
        base_path = Path(BASE_DIR / "cookiesFile").resolve()

        if not cookie_file_path.is_relative_to(base_path):
            return jsonify({
                "code": 500,
                "msg": "非法文件路径",
                "data": None
            }), 400

        if not cookie_file_path.exists():
            return jsonify({
                "code": 500,
                "msg": "Cookie文件不存在",
                "data": None
            }), 404

        # 返回文件
        return send_from_directory(
            directory=str(cookie_file_path.parent),
            path=cookie_file_path.name,
            as_attachment=True
        )

    except Exception as e:
        print(f"下载Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"下载Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# 包装函数：在线程中运行异步函数
def run_async_function(type,id,status_queue):
    match type:
        case '1':
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(xiaohongshu_cookie_gen(id, status_queue))
            loop.close()
        case '2':
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(get_tencent_cookie(id,status_queue))
            loop.close()
        case '3':
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(douyin_cookie_gen(id,status_queue))
            loop.close()
        case '4':
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(get_ks_cookie(id,status_queue))
            loop.close()

# ==================== 爆款采集与分析（hot_capture）====================

def _hot_modules():
    """延迟导入，避免 analyzer 依赖缺失时影响发布主流程。"""
    import sys as _sys
    hp = os.path.join(current_dir, "hot_capture")
    if hp not in _sys.path:
        _sys.path.insert(0, hp)
    import capture as _cap
    import analyzer as _ana
    return _cap, _ana


def _hot_all():
    """爆款工作台需要的全部子模块（延迟导入同一套 sys.path 处理）。"""
    _hot_modules()
    import domains as _dom
    import cover_cache as _cov
    import pipeline as _pipe
    import agent_cli as _agent
    import accounts as _acc
    return {"domains": _dom, "covers": _cov, "pipeline": _pipe, "agent": _agent, "accounts": _acc}


@app.route('/hotCapture/snapshots', methods=['GET'])
def hot_snapshots():
    """列出已落盘的爆款快照。"""
    try:
        _, ana = _hot_modules()
        return jsonify({"code": 200, "data": ana.list_snapshots(), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取快照列表失败: {e}"}), 500


@app.route('/hotCapture/analyze', methods=['POST'])
def hot_analyze():
    """对指定快照做爆款分析。body: {file?, use_llm?, save?}"""
    try:
        _, ana = _hot_modules()
        body = request.get_json(silent=True) or {}
        snap = ana.load_snapshot(body.get("file"))
        if snap is None:
            return jsonify({"code": 404, "data": None, "msg": "没有可用快照，请先采集"}), 404
        report = ana.analyze(snap, use_llm=bool(body.get("use_llm", True)))
        path = ""
        if body.get("save", True):
            path = str(ana.save_report(report))
        return jsonify({"code": 200, "data": {**report, "report_path": path}, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"分析失败: {e}"}), 500


@app.route('/hotCapture/report', methods=['GET'])
def hot_report():
    """读取最新分析报告。query: file?"""
    try:
        _, ana = _hot_modules()
        d = ana._capture.SNAPSHOT_DIR
        name = request.args.get("file")
        p = (d / (Path(name).name)) if name else next(
            (f for f in sorted(d.glob("report_*.json"), reverse=True)), None
        )
        if not p or not Path(p).exists():
            return jsonify({"code": 404, "data": None, "msg": "暂无分析报告"}), 404
        return jsonify({"code": 200, "data": json.loads(Path(p).read_text(encoding="utf-8")), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取报告失败: {e}"}), 500


@app.route('/hotCapture/config', methods=['GET', 'POST'])
def hot_config():
    """读取/写入 LLM 配置（api_key 脱敏返回）。"""
    try:
        _, ana = _hot_modules()
        if request.method == 'POST':
            body = request.get_json(silent=True) or {}
            # 与已有配置合并，避免只提交 4 个字段时把 agent_* 配置冲掉
            cfg = dict(ana.load_llm_config_file())
            for k in ("base_url", "model", "api_key", "timeout",
                      "agent_model", "agent_timeout", "agent_enabled"):
                if k in body:
                    cfg[k] = str(body.get(k, "")).strip()
            # 空 api_key 保留原值（前端脱敏显示时不回传）
            if not cfg.get("api_key"):
                cfg["api_key"] = ana.load_llm_config_file().get("api_key", "")
            ana.save_llm_config_file(cfg)
            return jsonify({"code": 200, "data": {"msg": "配置已保存"}, "msg": "ok"})
        cfg = ana.load_llm_config_file()
        masked = dict(cfg)
        if masked.get("api_key"):
            masked["api_key"] = masked["api_key"][:4] + "****" + masked["api_key"][-4:]
        return jsonify({"code": 200, "data": masked, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"配置操作失败: {e}"}), 500


@app.route('/hotCapture/domains', methods=['GET'])
def hot_domains():
    """可选领域与其关键词。"""
    try:
        m = _hot_all()
        return jsonify({"code": 200, "data": m["domains"].domain_options(), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取领域失败: {e}"}), 500


@app.route('/hotCapture/accounts', methods=['GET'])
def hot_accounts():
    """当前可用于抓取的登录态账号（来自「账号管理」页，不返回 cookie 内容）。

    query: platform=xiaohongshu|douyin
    """
    platform = (request.args.get("platform") or "xiaohongshu").strip()
    try:
        m = _hot_all()
        acc = m["accounts"]
        items = acc.list_accounts(platform)
        resolved = None
        try:
            r = m["pipeline"].resolve_auth(platform)
            resolved = {"mode": r["mode"], "name": (r.get("account") or {}).get("name", ""),
                        "logged_at": (r.get("account") or {}).get("logged_at", "")}
        except acc.AccountError as e:
            resolved = {"mode": "none", "error": str(e)}
        return jsonify({"code": 200, "data": {"platform": platform, "accounts": items,
                                             "resolved": resolved}, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取登录态失败: {e}"}), 500


@app.route('/hotCapture/capture', methods=['POST'])
def hot_capture_start():
    """建抓取任务。body: {mode, domains[], custom, limit, platform, use_mc, hot_top}"""
    try:
        m = _hot_all()
        body = request.get_json(silent=True) or {}
        try:
            limit = max(1, min(500, int(body.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        config = {
            "mode": "hot" if str(body.get("mode")) == "hot" else "domain",
            "domains": [str(d) for d in (body.get("domains") or [])][:12],
            "custom": str(body.get("custom", ""))[:300],
            "limit": limit,
            "platform": body.get("platform") if body.get("platform") in ("xiaohongshu", "douyin") else "xiaohongshu",
            "use_mc": bool(body.get("use_mc", True)),
            "hot_top": max(3, min(50, int(body.get("hot_top", 10) or 10))),
        }
        if config["mode"] == "domain" and not config["domains"] and not config["custom"]:
            return jsonify({"code": 400, "data": None, "msg": "请至少选一个领域或填一个自定义词"}), 400
        task = m["pipeline"].start_capture(config)
        return jsonify({"code": 200, "data": task, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"创建抓取任务失败: {e}"}), 500


@app.route('/hotCapture/task/<task_id>', methods=['GET'])
def hot_capture_task(task_id):
    """轮询抓取任务进度。"""
    try:
        m = _hot_all()
        t = m["pipeline"].load_task(task_id)
        if not t:
            return jsonify({"code": 404, "data": None, "msg": "任务不存在"}), 404
        return jsonify({"code": 200, "data": t, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取任务失败: {e}"}), 500


@app.route('/hotCapture/tasks', methods=['GET'])
def hot_capture_tasks():
    try:
        m = _hot_all()
        return jsonify({"code": 200, "data": m["pipeline"].recent_tasks(10), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取任务列表失败: {e}"}), 500


@app.route('/hotCapture/items', methods=['GET'])
def hot_capture_items():
    """列表页数据。query: snapshot?, platform?, keyword?, q?, sort?, limit?"""
    try:
        m = _hot_all()
        data = m["pipeline"].list_items(
            snapshot=request.args.get("snapshot") or None,
            platform=request.args.get("platform", ""),
            keyword=request.args.get("keyword", ""),
            q=request.args.get("q", ""),
            sort=request.args.get("sort", "likes"),
            limit=max(1, min(500, int(request.args.get("limit", 200) or 200))),
        )
        try:
            _, ana = _hot_modules()
            done = set(ana.analyzed_ids())
            for it in data["items"]:
                it["analyzed"] = f"{it.get('platform')}:{it.get('id')}" in done
        except Exception:
            pass
        return jsonify({"code": 200, "data": data, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取条目失败: {e}"}), 500


@app.route('/hotCapture/cover/<path:filename>', methods=['GET'])
def hot_cover(filename):
    """读本地缓存的封面图。"""
    try:
        m = _hot_all()
        d = m["covers"].COVER_DIR
        safe = secure_filename(filename) or os.path.basename(filename)
        if not (d / safe).exists():
            # secure_filename 会吃掉部分字符，回退到 basename 直读
            safe = os.path.basename(filename)
        if not (d / safe).exists():
            return jsonify({"code": 404, "data": None, "msg": "封面不存在"}), 404
        resp = send_from_directory(str(d), safe)
        # Windows 的 mimetypes 可能不认识 webp/avif，显式给出去，否则浏览器当二进制下载
        ext = os.path.splitext(safe)[1].lower()
        mime = {".webp": "image/webp", ".avif": "image/avif", ".png": "image/png",
                ".gif": "image/gif", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext)
        if mime:
            resp.headers["Content-Type"] = mime
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取封面失败: {e}"}), 500


@app.route('/hotCapture/analyzeItem', methods=['POST'])
def hot_analyze_item():
    """单条爆款分析（调 WorkBuddy agent）。body: {item?, platform?, id?, force?}"""
    try:
        _, ana = _hot_modules()
        body = request.get_json(silent=True) or {}
        item = body.get("item")
        if not isinstance(item, dict):
            m = _hot_all()
            data = m["pipeline"].load_latest() or {}
            key = (body.get("platform", ""), str(body.get("id", "")))
            for it in data.get("items", []):
                if (it.get("platform"), str(it.get("id"))) == key:
                    item = it
                    break
        if not isinstance(item, dict):
            return jsonify({"code": 400, "data": None, "msg": "找不到对应条目，请刷新列表后重试"}), 400
        result = ana.analyze_item(item, force=bool(body.get("force")))
        return jsonify({"code": 200, "data": result, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"单条分析失败: {e}"}), 500


@app.route('/hotCapture/agentStatus', methods=['GET'])
def hot_agent_status():
    """WorkBuddy CLI 可用性；?probe=1 会真跑一次短问答（约 10-30 秒）。"""
    try:
        _, ana = _hot_modules()
        return jsonify({"code": 200, "data": ana.agent_status(probe=request.args.get("probe") == "1"), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"探测 agent 失败: {e}"}), 500


# ==================== 创作工作台（creator）====================

def _creator():
    """延迟导入创作工作台模块，避免拖慢发布主流程。"""
    try:
        from creator import config as _cfg
        from creator import planner as _pl
        from creator import pipeline as _pipe
        from creator import comfy as _cf
        return {"config": _cfg, "planner": _pl, "pipeline": _pipe, "comfy": _cf}
    except ImportError:
        import sys as _sys
        cp = os.path.join(current_dir, "creator")
        if cp not in _sys.path:
            _sys.path.insert(0, cp)
        import config as _cfg
        import planner as _pl
        import pipeline as _pipe
        import comfy as _cf
        return {"config": _cfg, "planner": _pl, "pipeline": _pipe, "comfy": _cf}


def _load_analysis(item_id):
    """从爆款工作台的分析缓存里取一条结构拆解。item_id 支持 "douyin_123" 或纯 "123"。"""
    d = Path(current_dir) / "data" / "hot_capture" / "analysis"
    if not d.is_dir() or not item_id:
        return None
    iid = str(item_id).strip()
    for f in d.glob("*.json"):
        if f.stem == iid or f.stem.endswith("_" + iid):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
    return None


@app.route('/creator/status', methods=['GET'])
def creator_status():
    """环境自检：ComfyUI 在不在、权重齐不齐、ffmpeg 有没有、agent 可用不可用。"""
    try:
        import sys
        m = _creator()
        deep = request.args.get("deep") == "1"
        st = m["config"].probe(deep=deep)
        try:
            from hot_capture.agent_cli import status as _agent_status  # noqa
        except ImportError:
            sys_path_hot = os.path.join(current_dir, "hot_capture")
            if sys_path_hot not in sys.path:
                sys.path.insert(0, sys_path_hot)
            from agent_cli import status as _agent_status  # noqa
        a = _agent_status(probe=False)
        st["agent"] = {"available": a.get("available", False), "model": a.get("model", ""),
                       "cli": a.get("cli", "")}
        st["formats"] = m["config"].FORMATS
        st["max_shot_seconds"] = m["config"].load_config().get("max_shot_seconds", 5)
        return jsonify({"code": 200, "data": st, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"环境自检失败: {e}"}), 500


@app.route('/creator/config', methods=['GET', 'POST'])
def creator_config():
    """读取/保存 ComfyUI 连接与出片参数。"""
    try:
        m = _creator()
        if request.method == 'POST':
            body = request.get_json(silent=True) or {}
            cfg = m["config"].save_config(body)
            return jsonify({"code": 200, "data": cfg, "msg": "配置已保存"})
        return jsonify({"code": 200, "data": m["config"].load_config(), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"配置操作失败: {e}"}), 500


@app.route('/creator/plan', methods=['POST'])
def creator_make_plan():
    """生成创作方案。body: {mode, brief?, item_id?, extra?, format?, save?}

    mode=viral 时用 item_id 读爆款分析；mode=original/talking 时用 brief。
    """
    try:
        m = _creator()
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or "original").strip()
        fmt = str(body.get("format") or "portrait").strip()
        extra = str(body.get("extra") or "").strip()

        if mode == "viral":
            item_id = str(body.get("item_id") or "").strip()
            if not item_id:
                return jsonify({"code": 400, "data": None, "msg": "缺少 item_id"}), 400
            src = _load_analysis(item_id)
            if not src:
                return jsonify({"code": 404, "data": None,
                                "msg": "这条还没有分析结果，请先在爆款工作台点「分析」"}), 404
            res = m["planner"].from_source_item(item_id, src.get("analysis") or {},
                                                extra, fmt)
            # 把原爆款的身份信息补全，方案详情页要能点回原片
            ss = res["plan"].setdefault("source", {})
            ss.update({
                "item_id": item_id,
                "title": src.get("title") or ss.get("title") or "",
                "url": src.get("url") or ss.get("url") or "",
                "platform": src.get("platform") or "",
                "analysis_title": src.get("title", ""),
            })
        else:
            res = m["planner"].build(mode, {"brief": body.get("brief", ""), "extra": extra,
                                            "format": fmt})
        plan = res["plan"]
        # 失败（模型没给结构化结果）也要落盘成方案：用户写的内容不能因为一次失败就丢了
        if body.get("save", True):
            plan = m["pipeline"].save_plan(plan)
        return jsonify({"code": 200, "data": {"plan": plan, "ok": res["ok"],
                                              "error": res.get("error", ""),
                                              "ms": res.get("ms", 0)}, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"生成方案失败: {e}"}), 500


@app.route('/creator/drafts', methods=['GET', 'POST'])
@app.route('/creator/draft/<draft_id>', methods=['GET', 'DELETE'])
def creator_drafts(draft_id=None):
    """临时方案（草稿）：选题阶段写了一半的内容，随时存、随时还原。

    GET  /creator/drafts          列表
    POST /creator/drafts          保存（不传 id 就新建，传了就是更新）
    GET  /creator/draft/<id>      读一份
    DELETE /creator/draft/<id>    删一份（生成出方案后前端会主动删）
    """
    try:
        p = _creator()["pipeline"]
        if draft_id is None:
            if request.method == 'POST':
                body = request.get_json(silent=True) or {}
                return jsonify({"code": 200, "data": p.save_draft(body), "msg": "已保存临时方案"})
            return jsonify({"code": 200, "data": p.list_drafts(), "msg": "ok"})
        if request.method == 'DELETE':
            ok = p.delete_draft(draft_id)
            return jsonify({"code": 200 if ok else 404, "data": None,
                            "msg": "已删除" if ok else "临时方案不存在"})
        d = p.load_draft(draft_id)
        if not d:
            return jsonify({"code": 404, "data": None, "msg": "临时方案不存在"}), 404
        return jsonify({"code": 200, "data": d, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"临时方案操作失败: {e}"}), 500


@app.route('/creator/plans', methods=['GET'])
def creator_plans():
    try:
        return jsonify({"code": 200, "data": _creator()["pipeline"].list_plans(), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取方案列表失败: {e}"}), 500


@app.route('/creator/plan/<plan_id>', methods=['GET', 'PUT', 'DELETE'])
def creator_plan(plan_id):
    """读 / 存 / 删一份方案。PUT 用于前端改完分镜后落盘。"""
    try:
        p = _creator()["pipeline"]
        if request.method == 'DELETE':
            ok = p.delete_plan(plan_id)
            return jsonify({"code": 200 if ok else 404, "data": None,
                            "msg": "已删除" if ok else "方案不存在"})
        plan = p.load_plan(plan_id)
        if not plan:
            return jsonify({"code": 404, "data": None, "msg": "方案不存在"}), 404
        if request.method == 'PUT':
            body = request.get_json(silent=True) or {}
            for k in ("title", "hook", "script", "identity", "extra_note",
                      "target_audience", "angle", "publish_tips", "format"):
                if k in body:
                    plan[k] = body[k]
            if isinstance(body.get("shots"), list) and body["shots"]:
                plan["shots"] = body["shots"]
            if isinstance(body.get("hashtags"), list):
                plan["hashtags"] = body["hashtags"]
            plan = p.save_plan(plan)
        return jsonify({"code": 200, "data": plan, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"方案操作失败: {e}"}), 500


@app.route('/creator/plan/<plan_id>/render', methods=['POST'])
def creator_render(plan_id):
    """开始出片。body: {shots?: [idx,...]} 只重跑指定镜次。"""
    try:
        body = request.get_json(silent=True) or {}
        only = body.get("shots") if isinstance(body.get("shots"), list) else None
        only = [int(x) for x in only] if only else None
        r = _creator()["pipeline"].start_render(plan_id, only=only)
        return jsonify({"code": 200 if r["ok"] else 400,
                        "data": r.get("task"), "msg": r.get("error") or "已开始出片"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"启动出片失败: {e}"}), 500


@app.route('/creator/task/<task_id>', methods=['GET'])
def creator_task(task_id):
    try:
        t = _creator()["pipeline"].get_task(task_id)
        if not t:
            return jsonify({"code": 404, "data": None, "msg": "任务不存在"}), 404
        return jsonify({"code": 200, "data": t, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取任务失败: {e}"}), 500


@app.route('/creator/tasks', methods=['GET'])
def creator_tasks():
    try:
        return jsonify({"code": 200, "data": _creator()["pipeline"].list_tasks(), "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取任务列表失败: {e}"}), 500


@app.route('/creator/plan/<plan_id>/export', methods=['GET'])
def creator_export(plan_id):
    """导出口播提词卡 / 分镜表 / 字幕。query: kind=teleprompter|shotlist|srt"""
    try:
        p = _creator()["pipeline"]
        plan = p.load_plan(plan_id)
        if not plan:
            return jsonify({"code": 404, "data": None, "msg": "方案不存在"}), 404
        kind = (request.args.get("kind") or "teleprompter").strip()
        builder = {"teleprompter": p.build_teleprompter,
                   "shotlist": p.build_shotlist,
                   "srt": p.build_srt}.get(kind)
        if not builder:
            return jsonify({"code": 400, "data": None, "msg": "kind 只能是 teleprompter/shotlist/srt"}), 400
        return jsonify({"code": 200, "data": {"kind": kind, "text": builder(plan)}, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"导出失败: {e}"}), 500


@app.route('/creator/video/<plan_id>/<path:filename>', methods=['GET'])
def creator_video(plan_id, filename):
    """回放出片产物。支持 Range 请求，前端 <video> 才能拖进度。"""
    try:
        from creator import config as C
        d = C.VIDEO_DIR / Path(plan_id).name
        safe = Path(filename).name
        if not (d / safe).exists():
            return jsonify({"code": 404, "data": None, "msg": "视频不存在"}), 404
        return send_from_directory(str(d), safe, conditional=True)
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取视频失败: {e}"}), 500


@app.route('/creator/analysis/<item_id>', methods=['GET'])
def creator_analysis(item_id):
    """单条爆款分析的完整内容。方案里没存原片拆解时，前端用这个补上。"""
    try:
        src = _load_analysis(item_id)
        if not src:
            return jsonify({"code": 404, "data": None, "msg": "这条没有分析结果"}), 404
        return jsonify({"code": 200, "data": src, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取分析失败: {e}"}), 500


@app.route('/creator/analyzed', methods=['GET'])
def creator_analyzed():
    """已做过爆款分析、可直接拿来做「爆款复刻」的条目。"""
    try:
        d = Path(current_dir) / "data" / "hot_capture" / "analysis"
        out = []
        if d.is_dir():
            for f in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:80]:
                try:
                    j = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                a = j.get("analysis") or {}
                if not a:
                    continue
                out.append({
                    "key": f.stem,
                    "item_id": j.get("item_id", ""),
                    "platform": j.get("platform", ""),
                    "title": j.get("title", ""),
                    "url": j.get("url", ""),
                    "score": a.get("score", 0),
                    "verdict": a.get("verdict", ""),
                    "hook": a.get("hook", ""),
                    "engine": j.get("engine", ""),
                    "analyzed_at": j.get("analyzed_at", ""),
                })
        return jsonify({"code": 200, "data": out, "msg": "ok"})
    except Exception as e:
        return jsonify({"code": 500, "data": None, "msg": f"读取分析列表失败: {e}"}), 500


@app.route('/creator/presets', methods=['GET'])
def creator_presets():
    """页面上的快捷预设：常做内容方向的提示词种子。"""
    return jsonify({"code": 200, "data": {
        "formats": [{"value": k, "label": v["label"], "width": v["width"], "height": v["height"]}
                    for k, v in _creator()["config"].FORMATS.items()],
        "modes": [
            {"value": "viral", "label": "爆款复刻", "desc": "从已分析的爆款拆解里推出同构新片"},
            {"value": "original", "label": "完全自创", "desc": "给一个想法，直接出方案"},
            {"value": "talking", "label": "口播类", "desc": "出口播稿+提词卡+分镜，真人出镜拍"},
        ],
        "topics": ["家常菜教程", "租房改造", "通勤穿搭", "健身入门", "宠物日常",
                   "职场沟通", "学习方法", "数码好物", "旅行攻略", "情感观点"],
    }, "msg": "ok"})


# SSE 流生成器函数
def sse_stream(status_queue):
    while True:
        if not status_queue.empty():
            msg = status_queue.get()
            yield f"data: {msg}\n\n"
        else:
            # 避免 CPU 占满
            time.sleep(0.1)

if __name__ == '__main__':
    app.run(host='0.0.0.0' ,port=5409)
