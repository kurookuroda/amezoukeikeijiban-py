#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# amezo.cgi の Python 版
#   削除用合言葉は crypt をやめて PBKDF2-HMAC-SHA256 に変更。
#   ハッシュの作り方:  python3 amezo.py --make-hash 合言葉  > delpass.txt

import sys
import os
import re
import time
import fcntl
import hmac
import urllib.parse
import html
import signal
import hashlib
from datetime import datetime

# スクリプトのあるディレクトリに固定 (Perl の chdir($Bin) 相当)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# CGI 環境(LANG=C 等)でも日本語を UTF-8 で出力する
try:
    sys.stdout.reconfigure(encoding='utf-8', newline='')
except AttributeError:
    pass

# =========================================================================
# 設定
# =========================================================================
COPYRIGHT = """<!--
    Script for amezo-like BBS (Python Version)
    Original Copyright (C) 1999-2003 amezo_
    http://www17.big.or.jp/~amezo_/
    http://ame.x0.com/
-->
"""

AMEZO = '＠あめぞう'
URLBASE = 'http://ame.x0.com/'  # 実際の運用ドメインに合わせて変更
ADMIN = 'サポート'

POST_INTERVAL = 5       # 同じ板への連続投稿の最短間隔(秒)
MAX_THREADS = 0         # 板に残すスレッド数の上限。0なら無制限
PAGE_CACHE = 10         # 「次のページ」の再生成を使い回す秒数
TRUSTED_PROXIES = ['127.0.0.1', '::1']
DELCRYPTFILE = 'delpass.txt'

HASH_SCHEME = 'pbkdf2_sha256'
HASH_ITERATIONS = 100000

HEADER = "Content-type: text/html; charset=UTF-8\r\n\r\n"

# &amp; &quot; &lt; &gt; &#39; を1文字として数えるためのトークン
_ENTITY_TOKEN = re.compile(r'&(?:amp|quot|lt|gt|#39);|[\s\S]')
_INVISIBLE = re.compile(
    '[\u0080-\u009F\u00AD\u034F\u061C\u115F\u1160\u17B4\u17B5'
    '\u180B-\u180E\u200B\u200C\u200E\u200F\u2028-\u202E\u2060-\u206F'
    '\u3164\uFEFF\uFFA0\uFFF9-\uFFFB]'
)

# =========================================================================
# ユーティリティ関数
# =========================================================================

def error(msg):
    """エラーメッセージを出力して終了"""
    sys.stdout.write(HEADER)
    sys.stdout.write(
        "<html><head><meta http-equiv=\"Content-Type\" content=\"text/html; charset=UTF-8\">"
        f"</head><body><h2>{html.escape(msg)}</h2></body></html>"
    )
    sys.exit(0)


def to_utf8(b):
    """バイト列を str へ。UTF-8 → CP932 の順に厳密に試し、駄目なら高位バイトを捨てる"""
    if not b:
        return ""
    for enc in ('utf-8', 'cp932'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode('ascii', errors='ignore')


def strip_invisible(s):
    """不可視文字（ゼロ幅スペース等）を除去"""
    return _INVISIBLE.sub('', s)


def html_escape(s):
    """Perl 版と同じ順序・同じ実体参照でエスケープ"""
    return (s.replace('&', '&amp;')
             .replace('"', '&quot;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace("'", '&#39;'))


def html_chars_len(s):
    """実体参照を1文字と数えた長さ"""
    return len(_ENTITY_TOKEN.findall(s))


def ccut(s, max_len):
    """実体参照の途中で切らない文字数切り詰め"""
    return ''.join(_ENTITY_TOKEN.findall(s)[:max_len])


def url_decode(s):
    """%XX と + を復号して str に (Perl の tr/+/ / と %XX 展開に相当)"""
    if isinstance(s, str):
        s = s.encode('latin1', errors='ignore')
    return to_utf8(urllib.parse.unquote_to_bytes(s.replace(b'+', b' ')))


def client_ip():
    """接続元IP取得（信頼プロキシ考慮）"""
    addr = os.environ.get('REMOTE_ADDR', '')
    if addr not in TRUSTED_PROXIES:
        return addr
    ip_re = re.compile(r'^[0-9A-Fa-f:.]{2,45}$')
    cf = os.environ.get('HTTP_CF_CONNECTING_IP', '')
    if cf and ip_re.match(cf):
        return cf
    xff = [p.strip() for p in os.environ.get('HTTP_X_FORWARDED_FOR', '').split(',')]
    if xff and ip_re.match(xff[-1]):
        return xff[-1]
    return addr


# ---- 合言葉ハッシュ (PBKDF2-HMAC-SHA256) ---------------------------------

def make_hash(password, iterations=HASH_ITERATIONS, salt=None):
    """pbkdf2_sha256$反復回数$salt(hex)$hash(hex) 形式のハッシュを作る"""
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f"{HASH_SCHEME}${iterations}${salt.hex()}${dk.hex()}"


def verify_hash(password, stored):
    """make_hash の形式のハッシュと照合"""
    try:
        scheme, iters, salt_hex, hash_hex = stored.strip().split('$')
        if scheme != HASH_SCHEME:
            return False
        iters = int(iters)
        if not (1 <= iters <= 5000000):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iters)
    return hmac.compare_digest(dk, expected)


def normalize_password(raw):
    """投稿者欄に入れた文字列がサーバー側で受ける加工と同じ加工をする"""
    s = html_escape(strip_invisible(raw))
    s = re.sub(' |　|\u200D', '', s)
    return ccut(s, 30)


def load_delcrypt():
    """削除用ハッシュ読み込み"""
    try:
        with open(DELCRYPTFILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


# ---- 板・投稿間隔 -----------------------------------------------------------

def is_board(folder):
    """板名が blist.txt に存在するか確認"""
    try:
        with open('./blist.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if line and line.split(',')[0] == folder:
                    return True
    except FileNotFoundError:
        pass
    return False


def post_interval_left(folder):
    """前回の投稿からの経過時間をチェック"""
    if POST_INTERVAL <= 0:
        return 0
    last = 0
    try:
        with open(f'./{folder}/lasttime.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content.isdigit():
                last = int(content)
    except FileNotFoundError:
        pass
    left = POST_INTERVAL - (int(time.time()) - last)
    return left if left > 0 else 0


def touch_lasttime(folder):
    """最終投稿時刻を記録"""
    if POST_INTERVAL <= 0:
        return
    try:
        with open(f'./{folder}/lasttime.txt', 'w', encoding='utf-8') as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def check_global_ip(ip):
    """IPベースの連投制限 (global.mem)。ロックは読み書きの間ずっと保持する"""
    try:
        fd = os.open('global.mem', os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return
    with os.fdopen(fd, 'r+', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        now = int(time.time())
        iplist = {}
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit() and now - int(parts[1]) < 180:
                iplist[parts[0]] = int(parts[1])
        if ip in iplist:
            remain = 180 - (now - iplist[ip])
            error(f"あと {remain} 秒たってから投稿してください")
        iplist[ip] = now
        f.seek(0)
        f.truncate()
        for k, v in iplist.items():
            f.write(f"{k} {v}\n")


def expand_range(lo, hi):
    """a-b を a,a+1,...,b に展開 (最大1001件)"""
    lo = int(lo)
    hi = int(hi)
    if hi - lo > 1000:
        hi = lo + 1000
    if lo <= hi:
        return ','.join(str(x) for x in range(lo, hi + 1))
    return ''


def redirect(folder, index_name):
    # CGI と同じ階層に板フォルダがある前提の相対URL (URLBASE に依存しない)
    target = html.escape(f"{folder}/{index_name}", quote=True)
    sys.stdout.write("Status: 303 See Other\r\n")
    sys.stdout.write(f"Location: {target}\r\n")
    sys.stdout.write(HEADER)
    sys.stdout.write(f'<html><head><meta http-equiv="refresh" content="0;url={target}"></head>')
    sys.stdout.write(f'<body>Redirecting to <a href="{target}">{target}</a>...</body></html>')
    sys.exit(0)


# =========================================================================
# レス表示 / 管理者削除
# =========================================================================

def res_view(folder, form):
    """レス表示 (Perl の res サブルーチン相当)"""
    res_id = form.get('res', '')
    frm = form.get('from', '')
    frm = int(frm) if re.fullmatch(r'[0-9]+', frm or '') else 1

    try:
        f = open(f"./{folder}/{res_id}.html", 'r', encoding='utf-8')
    except FileNotFoundError:
        error("スレッドがありません.")

    with f:
        sys.stdout.write(HEADER)
        sys.stdout.write(f.readline())
        for line in f:
            m = re.search(r'<dt>(\d+)', line)
            if m and int(m.group(1)) >= frm:
                sys.stdout.write(line)
                break
        fol_input = f'<input type=hidden name="folder" value="{folder}">'
        for line in f:
            if '</form>' in line:
                line = line.replace('action="../', 'action="', 1)
                line = line.replace('</form>', fol_input + '</form>', 1)
            sys.stdout.write(line)
    sys.exit(0)


def admin_delete(folder, res, comm):
    """スレッド/レスの削除 (Perl の del サブルーチン相当)"""
    if not res:
        sys.stdout.write(HEADER)
        sys.exit(0)

    file_path = f"./{folder}/{res}.html"

    # コメント欄が空ならスレッドごと x付きにリネーム
    if not comm:
        newfile = f"./{folder}/x{res}.html"
        try:
            os.rename(file_path, newfile)
        except OSError:
            error("スレッドがありません.")
        os.chmod(newfile, 0o600)
        sys.stdout.write(HEADER)
        sys.exit(0)

    comm_list = ',' + comm + ','
    comm_list = re.sub(r'(\d{1,9})-(\d{1,9})',
                       lambda m: expand_range(m.group(1), m.group(2)), comm_list)
    comm_list = comm_list.replace('<br>', ',')

    try:
        f = open(file_path, 'r+', encoding='utf-8')
    except FileNotFoundError:
        error("スレッドがありません.")

    with f:
        fcntl.flock(f, fcntl.LOCK_EX)
        orig_mtime = os.fstat(f.fileno()).st_mtime
        keep = []
        stamp = None
        for line in f:
            m = re.search(r'<dt>(\d+)', line)
            if m and (',' + m.group(1) + ',') in comm_list:
                continue
            d = re.search(r'(\d{4})年(\d{2})月(\d{2})日\([^)]+\)(\d{2})時(\d{2})分(\d{2})秒', line)
            if d:
                stamp = tuple(int(d.group(i)) for i in range(1, 7))
            keep.append(line)
        f.seek(0)
        f.truncate()
        f.writelines(keep)

    # 更新日時を最後に残ったレスの投稿時刻へ (Perl の timelocal 相当 = ローカル時刻)
    ts = orig_mtime
    if stamp:
        try:
            ts = time.mktime(stamp + (0, 0, -1))
        except (OverflowError, ValueError):
            pass
    m = re.search(r'p(\d+)', comm_list)
    if m:
        ts -= int(m.group(1)) * 24 * 60 * 60
    os.utime(file_path, (ts + 1, ts + 1))
    if 'x' in comm_list:
        os.chmod(file_path, 0o404)

    sys.stdout.write(HEADER)
    sys.exit(0)


# =========================================================================
# メイン処理
# =========================================================================

def main():
    amezo = AMEZO

    cgi = os.path.basename(os.environ.get('SCRIPT_FILENAME', '')) or 'amezo.cgi'
    cgi = re.sub(r'[\r\n]', '', cgi)

    # ---- GET パラメータ ----
    query_params = {}
    qs = os.environ.get('QUERY_STRING', '')
    if qs:
        for pair in qs.split('&'):
            k, _, v = pair.partition('=')
            query_params[k] = url_decode(v)

    # ---- POST 本体 ----
    cl = os.environ.get('CONTENT_LENGTH', '') or '0'
    if not re.fullmatch(r'[0-9]+', cl) or int(cl) > 7500:
        error('投稿内容が大きすぎます')
    buffer = sys.stdin.buffer.read(int(cl))
    if len(buffer) > 7500:
        error('投稿内容が大きすぎます')

    form = {}
    for pair in buffer.split(b'&'):
        if b'=' not in pair:
            continue
        k, v = pair.split(b'=', 1)
        key = k.decode('latin1')
        value = url_decode(v)
        value = strip_invisible(value)
        value = html_escape(value)
        value = value.replace('\r\n', '\r').replace('\n', '\r')
        if key == 'comm':
            value = value.replace('\r', '<br>')
        value = re.sub(r'[\x00-\x1f]', '', value)
        form[key] = value

    # ---- 板名（フォルダ）決定 ----
    urlbase_cmp = re.sub(r'^(https?://)www\.', r'\1', URLBASE)

    folder = ''
    if form.get('folder'):
        folder = form['folder']
    elif query_params.get('folder'):
        folder = query_params['folder']
    else:
        ref = urllib.parse.unquote(os.environ.get('HTTP_REFERER', ''))
        ref = re.sub(r'^(https?://)www\.', r'\1', ref)
        m = re.search(r'https?://[^/]+/([^/]+)/', ref)
        if m:
            folder = m.group(1)

    if not folder:
        error('板名が判定できません。')
    if '/' in folder or '..' in folder or not is_board(folder):
        error('板名が不正です')

    # ---- Origin / Referer 検証 ----
    origin = os.environ.get('HTTP_ORIGIN', '')
    referer = os.environ.get('HTTP_REFERER', '')
    http_host = re.sub(r':\d+$', '', os.environ.get('HTTP_HOST', ''))

    if origin:
        origin_cmp = re.sub(r'^(https?://)www\.', r'\1', origin)
        if not urlbase_cmp.startswith(origin_cmp + '/'):
            if http_host and http_host not in origin_cmp:
                error('不正なリクエストです(Origin不一致)')
    elif referer:
        if http_host and http_host not in referer and URLBASE not in referer:
            error('不正なリクエストです(Referer不一致)')
    # Origin も Referer も無い場合は通す (Colab + トンネル環境を考慮)

    name = form.get('name', '')
    mail = form.get('mail', '')
    comm = form.get('comm', '')
    subj = form.get('subj', '')

    res = form.get('res', '')
    if not re.fullmatch(r'[0-9]*', res):
        error('不正なスレッド指定です')

    # 「〜」ボタン: res 表示へ
    if form.get('sub'):
        res_view(folder, form)

    nx = form.get('next', '')
    next_page = int(nx) if re.fullmatch(r'[0-9]{1,6}', nx) and int(nx) > 0 else 1
    next_page += 20

    reload_ = False
    if name == subj and name == comm:
        reload_ = True
    if not name and not subj and not comm:
        reload_ = True

    if comm.count('<br>') > 20:
        error("改行が多すぎます")
    if html_chars_len(subj) > 20:
        error("題名が長すぎます")

    name = re.sub(' |　|\u200D', '', name)
    name = ccut(name, 30)
    comm = re.sub(r'&gt;(&gt;)+', '&gt;', comm)
    subj = re.sub(' |　|＿', '', subj)

    # ---- NGワード ----
    comm2 = re.sub(r'%([0-7][0-9a-fA-F])', lambda m: chr(int(m.group(1), 16)), comm)
    try:
        with open('./ng.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if not line:
                    continue
                ng, _, opt = line.partition(',')
                if not ng:
                    continue
                ng_re = re.escape(ng)
                if opt == 'n':
                    if re.search(ng_re, name):
                        reload_ = True
                elif opt == 'c':
                    if re.search(ng_re, comm2):
                        reload_ = True
                else:
                    name = re.sub(ng_re, '', name)
                    comm = re.sub(ng_re, '', comm)
                    mail = re.sub(ng_re, '', mail)
                    subj = re.sub(ng_re, '', subj)
    except FileNotFoundError:
        pass

    # =====================================================================
    # 投稿処理
    # =====================================================================
    if next_page == 21 and not reload_:
        name = name.replace(ADMIN, '＃' + ADMIN)

        delcrypt = load_delcrypt()
        if delcrypt and verify_hash(name, delcrypt):
            if mail:
                # 管理者名での通常投稿
                name = ADMIN
                if '@' not in mail:
                    mail = ''
            else:
                admin_delete(folder, res, comm)   # 内部で終了する

        if res:
            subj = ''

        if not name:
            error("投稿者を記入してください")
        if not comm:
            error("コメントが入力されていません")
        if not res and not subj:
            error("題名が入力されていません")

        left = post_interval_left(folder)
        if left > 0:
            error(f"連投を検知しました。あと {left} 秒ほど待ってから投稿してください")

        if subj:
            check_global_ip(client_ip())

        # URL自動リンク (\w は ASCII のみ。日本語を巻き込まない)
        def url_replacer(m):
            dsp = m.group(1)
            url = dsp.replace('&amp;', '&')
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer nofollow">{dsp}</a>'
        comm = re.sub(r'(https?:[\w.~\-/?+=:@%;#&]+)', url_replacer, comm, flags=re.ASCII)

        if mail:
            name = f'<a href="mailto:{mail} ">{name} </a>'

        # 日時
        now = datetime.now()
        yy = now.year % 100
        wday = "日月火水木金土"[(now.weekday() + 1) % 7]   # weekday(): 月=0
        date_str = (f"20{yy:02d}年{now.month:02d}月{now.day:02d}日({wday})"
                    f"{now.hour:02d}時{now.minute:02d}分{now.second:02d}秒")
        if not res:
            res = f"{yy:02d}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}{now.second:02d}"

        # ---- スレッドファイルを開く ----
        fd = -1
        if subj:
            for trial in range(31):
                file_path = f"./{folder}/{res}.html"
                if not os.path.exists(f"./{folder}/x{res}.html") and \
                   not os.path.exists(f"./{folder}/o{res}.html"):
                    try:
                        fd = os.open(file_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
                        break
                    except FileExistsError:
                        pass
                if trial >= 30:
                    error("スレッドを作成できません.")
                res = str(int(res) + 1)
        else:
            file_path = f"./{folder}/{res}.html"
            try:
                fd = os.open(file_path, os.O_RDWR)
            except FileNotFoundError:
                error("スレッドがありません.")

        with os.fdopen(fd, 'r+', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_EX)

            # 末尾のフォーム行の手前まで読み進める
            cnt = 0
            pos = 0
            prev = ""
            while True:
                line = f.readline()
                if not line:
                    break
                m = re.search(r'<dt>(\d+)', line)
                if not m:
                    break
                cnt = int(m.group(1))
                pos = f.tell()
                prev = line
            f.seek(pos)
            cnt += 1

            # 二重投稿チェック (Perl と同じ貪欲マッチ)
            m = re.search(r'<b>(.+) </b>.+<dd>(.+) </dl>', prev)
            if m and name == m.group(1) and comm == m.group(2):
                error("二重カキコです.")

            subj1 = ('<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                     f'<title>{subj} </title>') if subj else ''
            subj2 = f'<font size=+2 color=red><b>{subj} </b></font>' if subj else ''

            line_out = (f'{subj1}<dl><dt>{cnt}{subj2}投稿者：<font color=forestgreen><b>{name} </b></font>'
                        f'<font size=-1>　投稿日：{date_str}</font><br><dd>{comm} </dl>')
            form_html = (f'<form method=post action="../{cgi}">'
                         f'<input type=hidden name="res" value="{res}">'
                         '投稿者 <input type=text name="name" size=20>'
                         'メール <input type=text name="mail" size=20><br>'
                         '<input type=submit value="↑へのレスカキコ" >'
                         '<textarea name="comm" rows=4 cols=70 ></textarea>'
                         '</form>')
            f.write(line_out + '\n')
            f.write(form_html + '\n')
            f.truncate()
        # with を抜けた時点で close され、ロックも解放される

        touch_lasttime(folder)

    # =====================================================================
    # インデックス再生成
    # =====================================================================
    color = ""
    title = ""
    try:
        with open('./blist.txt', 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.rstrip('\r\n').split(',')
                if parts and parts[0] == folder:
                    color = parts[1] if len(parts) > 1 else ""
                    title = parts[2] if len(parts) > 2 else ""
                    break
    except FileNotFoundError:
        pass

    page_suffix = '2' if next_page != 21 else ''
    index_name = f"index{page_suffix}.html"              # 1ページ目は index.html
    redirect_name = index_name if page_suffix else ''    # 1ページ目の転送先は folder/
    indexfile = f"./{folder}/{index_name}"
    main_str = "(メイン)" if folder == 'main' else ""

    bg = ""
    if '!' in color:
        color, bg = color.split('!', 1)
        bg = f' background="{bg}"'

    text_attr = ""
    if color.endswith('*'):
        text_attr = ' text="#ffffff" link="red" vlink="orange"'
        color = color[:-1]

    title = title.replace('#c#', ',')
    title0 = title
    m = re.search(r'alt="([^"]+)', title0)
    if m:
        title0 = m.group(1)
        amezo = ""

    # リロードのみで直近に更新済みなら再生成しない
    if next_page == 21 and reload_ and POST_INTERVAL > 0:
        try:
            if time.time() - os.path.getmtime(indexfile) < POST_INTERVAL:
                redirect(folder, redirect_name)
        except FileNotFoundError:
            pass

    # 「次のページ」のキャッシュ
    if next_page != 21 and PAGE_CACHE > 0:
        try:
            with open(indexfile, 'r', encoding='utf-8') as f:
                head = f.read(4096)
            m = re.search(r'<!-- AMEZO-NEXT (\d+) -->', head)
            if m and int(m.group(1)) == next_page:
                if time.time() - os.path.getmtime(indexfile) < PAGE_CACHE:
                    redirect(folder, redirect_name)
        except (OSError, UnicodeDecodeError):
            pass

    # ---- 再生成ロック ----
    try:
        lk_fd = os.open(f"./{folder}/regen.lock", os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        error("ロックファイルを作成できません.")

    locked = False
    if next_page == 21 and not reload_:
        def on_alarm(signum, frame):
            raise TimeoutError()
        signal.signal(signal.SIGALRM, on_alarm)
        signal.alarm(10)
        try:
            fcntl.flock(lk_fd, fcntl.LOCK_EX)
            locked = True
        except TimeoutError:
            pass
        finally:
            signal.alarm(0)
    else:
        try:
            fcntl.flock(lk_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            pass

    if not locked:
        redirect(folder, redirect_name)

    # ---- スレッド一覧(更新日時の新しい順) ----
    entries = []
    try:
        for fname in os.listdir(f"./{folder}"):
            if re.fullmatch(r'\d+\.html', fname):
                try:
                    ts = os.stat(f"./{folder}/{fname}").st_mtime
                except FileNotFoundError:
                    continue
                entries.append((f"{int(ts):011d}", fname))
    except FileNotFoundError:
        pass
    entries.sort(key=lambda x: (x[0], x[1]), reverse=True)

    if MAX_THREADS > 0:
        kept = []
        for n, (ts, fname) in enumerate(entries, 1):
            if n > MAX_THREADS:
                newf = f"./{folder}/o{fname}"
                if not os.path.exists(newf):
                    try:
                        os.rename(f"./{folder}/{fname}", newf)
                        continue
                    except OSError:
                        pass
            kept.append((ts, fname))
        entries = kept

    tmpfile = f"{indexfile}.tmp{os.getpid()}"
    try:
        with open(tmpfile, 'w', encoding='utf-8') as f:
            f.write(f"""<html><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title0}{amezo}{main_str}</title></head>
{COPYRIGHT}<!-- AMEZO-NEXT {next_page} -->
<body{bg} bgcolor="{color}"{text_attr}>
<font size=+2>{title}{amezo}{main_str}</font>
""")

            try:
                with open('./header.html', 'r', encoding='utf-8') as hf:
                    f.write(hf.read())
            except FileNotFoundError:
                pass

            f.write(f"""<form method=post action="../{cgi}">
<input type=submit value="次のページ">
<input type=text name="next" value="{next_page}" size="3">〜</form>
<font size=2>
<!-- HEAD LINE -->
""")

            start_idx = next_page - 21

            # 見出し一覧
            for i in range(start_idx, start_idx + 100):
                if i >= len(entries):
                    break
                fname = entries[i][1]
                ttl = ''
                try:
                    with open(f"./{folder}/{fname}", 'r', encoding='utf-8') as rf:
                        m = re.search(r'<b>([^<]*)</b>', rf.readline())
                    ttl = m.group(1) if m else ''
                except OSError:
                    pass
                f.write(f' {i + 1}<a href="{fname}">{ttl}</a>\n')

            f.write("<!-- HEAD LINE END -->\n</font>\n")

            # 本文(1レス目 + 末尾7レス)
            for i in range(start_idx, next_page - 1):
                if i >= len(entries):
                    break
                fname = entries[i][1]
                try:
                    with open(f"./{folder}/{fname}", 'r', encoding='utf-8') as rf:
                        first = rf.readline()
                        rest = []
                        for line in rf:
                            if line.startswith('<form'):
                                break
                            rest.append(line)
                except OSError:
                    continue

                first = re.sub(r'<meta[^>]*>', '', first)
                first = re.sub(r'<title>.*?</title>', '', first)
                j = len(rest)

                res_num = fname[:-5]
                sub_form = ""
                if j > 200:
                    sub_form = (f'<input type=text name="from" value="{j - 98}" size="4">\n'
                                '<input type=submit name="sub" value="\u301c">\n')

                f.write('<table border=1 cellspacing=1 cellpadding=2 width=100% bgcolor="#efefef"><tr><td><font color="#000000">\n')
                f.write(first)
                f.write(''.join(rest[-7:]))
                f.write(f"""</font></td></tr></table>
<form method=post action="../{cgi}">
<input type=hidden name="res" value="{res_num}">
投稿者 <input type=text name="name" size=20>
メール <input type=text name="mail" size=20><br>
<input type=submit value="↑へのレスカキコ" >
<a href="{fname}">レス全部を見る</a>
{sub_form}<a href="#top">上へ　</a><a href="./">リロード</a><br>
<textarea name="comm" rows=4 cols=70 ></textarea>
</form><hr>
""")

            f.write(f"""<form method=post action="../{cgi}">
<input type=submit value="次のページ">
<input type=text name="next" value="{next_page}" size="3">〜</form>
<hr></body></html>
""")
    except OSError:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass
        error("indexを書き込めません.")

    try:
        try:
            mode = os.stat(indexfile).st_mode & 0o7777
        except FileNotFoundError:
            mode = 0o644
        os.chmod(tmpfile, mode)
        os.rename(tmpfile, indexfile)
    except OSError:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass
        error("indexを更新できません.")

    fcntl.flock(lk_fd, fcntl.LOCK_UN)
    os.close(lk_fd)

    redirect(folder, redirect_name)


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--make-hash':
        print(make_hash(normalize_password(sys.argv[2])))
    else:
        main()
