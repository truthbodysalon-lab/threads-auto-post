#!/usr/bin/env python3
"""stand.fm ラジオ更新の Threads(@masahide_takahashi_) 告知 — クラウド保険版（2026-11-25）。
Mac主: launchd com.mt112.podcast-threads-announce（7:20/9:20/12:20）。Macが止まっていた日だけ、中央ディスパッチャがこれを起動する。
- 二重投稿ガード: Threads API で直近の自分の投稿に、その回のURLが既にあれば何もしない（Mac・クラウドどちらが出していても検知）
- 公開から36時間以内・未告知の回を1回に1本だけ。古い回は遡らない
- DRY_RUN=1: 投稿せず、判定結果と本文だけ出す
- 通知は Discord のみ（DISCORD_WEBHOOK_URL）。LINEは使わない
"""
import html, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RSS = 'https://stand.fm/rss/6a1a95275a8c4d39c4a207dd'
API = 'https://graph.threads.net/v1.0'
WINDOW = timedelta(hours=36)
JST = timezone(timedelta(hours=9))
DRY = bool(os.environ.get('DRY_RUN'))


def log(m):
    print(f'[podcast-announce {datetime.now(JST):%H:%M:%S}] {m}', flush=True)


def notify(text):
    url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not url or DRY:
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({'content': text}).encode(),
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'podcast-announce'})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as ex:
        log(f'Discord通知失敗: {type(ex).__name__}')


def http(req):
    return urllib.request.urlopen(req, timeout=30).read()


def fetch_items():
    x = http(urllib.request.Request(RSS, headers={'User-Agent': 'podcast-announce/1.0'})).decode('utf-8')
    items = []
    for block in re.findall(r'<item>(.*?)</item>', x, re.S):
        def tag(t):
            m = re.search(rf'<{t}>(.*?)</{t}>', block, re.S)
            v = m.group(1).strip() if m else ''
            v = re.sub(r'^<!\[CDATA\[(.*)\]\]>$', r'\1', v, flags=re.S)
            return html.unescape(v)
        items.append({'title': tag('title'), 'link': tag('link'), 'pub': parsedate_to_datetime(tag('pubDate')), 'desc': tag('description')})
    return items


def hook_for(item, hooks):
    m = re.match(r'#(\d+)', item['title'])
    if m and m.group(1) in hooks:
        return hooks[m.group(1)]
    text = re.sub(r'<br\s*/?>', '\n', item['desc'])
    text = re.sub(r'<[^>]+>', '', text)
    for line in text.splitlines():
        line = line.strip().lstrip('・').strip()
        if line and 'lin.ee' not in line and not line.startswith('▼'):
            return line
    return ''


def build_text(item, hooks):
    parts = ['ラジオ更新しました。\n' + item['title']]
    h = hook_for(item, hooks)
    if h:
        parts.append(h)
    parts.append(item['link'])
    return '\n\n'.join(parts)


def recent_texts(token, uid):
    """直近の自分の投稿本文（最大100件）。取得失敗は例外のまま上げる＝投稿しない（二重投稿より未投稿を選ぶ）。"""
    q = urllib.parse.urlencode({'fields': 'id,text,timestamp', 'limit': 100, 'access_token': token})
    d = json.loads(http(urllib.request.Request(f'{API}/{uid}/threads?{q}')))
    return [p.get('text') or '' for p in d.get('data', [])]


def post(text, token, uid):
    data = urllib.parse.urlencode({'media_type': 'TEXT', 'text': text, 'access_token': token}).encode()
    cid = json.loads(http(urllib.request.Request(f'{API}/{uid}/threads', data=data, method='POST')))['id']
    time.sleep(5)
    data = urllib.parse.urlencode({'creation_id': cid, 'access_token': token}).encode()
    return json.loads(http(urllib.request.Request(f'{API}/{uid}/threads_publish', data=data, method='POST')))['id']


def main():
    token, uid = os.environ.get('THREADS_ACCESS_TOKEN_MASA'), os.environ.get('THREADS_USER_ID_MASA')
    if not token or not uid:
        log('Secrets不足 → 何もしない'); return 1
    try:
        hooks = json.loads((HERE / 'podcast_announce_hooks.json').read_text())
    except Exception:
        hooks = {}
    try:
        items = fetch_items()
    except Exception as ex:
        log(f'RSS取得失敗: {type(ex).__name__} → 何もしない'); return 1
    now = datetime.now(timezone.utc)
    cands = [i for i in items if timedelta(0) <= now - i['pub'] <= WINDOW]
    if not cands:
        log('36時間以内の新しい回なし'); return 0
    try:
        texts = recent_texts(token, uid)
    except Exception as ex:
        log(f'Threads投稿一覧の取得失敗: {type(ex).__name__} → 二重投稿を避けるため何もしない')
        notify('⚠ ラジオのThreads告知(クラウド保険): 投稿一覧が取れず判定不能のため見送りました'); return 1
    todo = [i for i in cands if not any(i['link'] in t for t in texts)]
    if not todo:
        log(f'対象{len(cands)}本は全て告知済み(Threads上で確認) → 何もしない'); return 0
    it = sorted(todo, key=lambda i: i['pub'])[0]
    text = build_text(it, hooks)
    if DRY:
        log(f'DRY_RUN: 告知するはずの回 {it["title"]}\n--- 本文 ---\n{text}'); return 0
    try:
        pid = post(text, token, uid)
    except Exception as ex:
        log(f'告知失敗 {it["title"]}: {type(ex).__name__}')
        notify(f'❌ ラジオのThreads告知(クラウド保険)に失敗: {it["title"]}'); return 1
    log(f'告知 {it["title"]} → post {pid}')
    notify(f'📻 ラジオをThreads(@masahide_takahashi_)で告知しました（Macが止まっていたためクラウド保険）\n{it["title"]}\n{it["link"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
