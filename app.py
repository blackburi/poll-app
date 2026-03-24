import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, abort, flash, g, redirect, render_template, request, url_for

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'polls.db')
KST = ZoneInfo('Asia/Seoul')
UTC = timezone.utc

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')
app.config['DATABASE'] = os.getenv('DATABASE_PATH', DB_PATH)
app.config['FINALIZE_TOKEN'] = os.getenv('FINALIZE_TOKEN', 'change-me-finalize-token')
app.config['WEBHOOK_URL'] = os.getenv('DISCORD_WEBHOOK_URL', '')
app.config['ADMIN_NICKNAME'] = os.getenv('ADMIN_NICKNAME', '관리자')
app.config['ADMIN_CODES'] = [
    code.strip() for code in os.getenv('ADMIN_CODES', '').split(',') if code.strip()
]


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.before_request
def ensure_db():
    db = get_db()
    try:
        db.execute("SELECT 1 FROM polls LIMIT 1")
    except Exception:
        with app.open_resource('schema.sql') as f:
            db.executescript(f.read().decode('utf-8'))
            db.commit()


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.execute('PRAGMA foreign_keys = ON')
    with open(os.path.join(BASE_DIR, 'schema.sql'), 'r', encoding='utf-8') as f:
        db.executescript(f.read())
    db.commit()
    db.close()


def kst_now():
    return datetime.now(KST)


def to_kst(dt_str: str) -> datetime:
    return datetime.strptime(dt_str, '%Y-%m-%dT%H:%M').replace(tzinfo=KST)


def from_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)


def to_display(dt_str: str) -> str:
    return from_iso(dt_str).astimezone(KST).strftime('%Y-%m-%d %H:%M')


def normalize_multiline_options(raw: str):
    seen = set()
    items = []
    for line in raw.splitlines():
        value = line.strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def poll_state(poll) -> str:
    now = kst_now()
    start_dt = from_iso(poll['start_at']).astimezone(KST)
    end_dt = from_iso(poll['end_at']).astimezone(KST)
    if poll['status'] == 'closed' or end_dt <= now:
        return 'closed'
    if start_dt > now:
        return 'scheduled'
    return 'open'


def fetch_poll_or_404(poll_id: int):
    db = get_db()
    poll = db.execute('SELECT * FROM polls WHERE id = ?', (poll_id,)).fetchone()
    if poll is None:
        abort(404)
    return poll


def fetch_poll_detail(poll_id: int):
    db = get_db()
    poll = fetch_poll_or_404(poll_id)
    options = db.execute(
        """
        SELECT * FROM poll_options WHERE poll_id = ? ORDER BY display_order, id
        """,
        (poll_id,),
    ).fetchall()
    votes = db.execute(
        """
        SELECT v.*, o.option_text
        FROM votes v
        JOIN poll_options o ON o.id = v.option_id
        WHERE v.poll_id = ?
        ORDER BY v.created_at ASC, v.id ASC
        """,
        (poll_id,),
    ).fetchall()
    comments = db.execute(
        """
        SELECT * FROM comments
        WHERE poll_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (poll_id,),
    ).fetchall()
    return poll, options, votes, comments


def build_poll_view_model(poll_id: int):
    poll, options, votes, comments = fetch_poll_detail(poll_id)

    grouped_votes = defaultdict(list)
    counts = defaultdict(int)
    for vote in votes:
        grouped_votes[vote['option_id']].append(vote)
        counts[vote['option_id']] += 1

    option_cards = []
    for option in options:
        option_cards.append(
            {
                'id': option['id'],
                'text': option['option_text'],
                'votes': grouped_votes[option['id']],
                'count': counts[option['id']],
            }
        )

    state = poll_state(poll)
    total_votes = len(votes)

    return {
        'poll': poll,
        'options': option_cards,
        'comments': comments,
        'total_votes': total_votes,
        'is_closed': state == 'closed',
        'is_scheduled': state == 'scheduled',
        'can_vote': state == 'open',
        'start_at_display': to_display(poll['start_at']),
        'end_at_display': to_display(poll['end_at']),
        'state': state,
        'admin_nickname': app.config['ADMIN_NICKNAME'],
    }


def format_created_message(poll_id: int) -> str:
    data = build_poll_view_model(poll_id)
    poll = data['poll']
    lines = [
        '🆕 새 투표가 생성됐어',
        f"제목: {poll['title']}",
        f"개설자: {poll['creator_nickname']}",
        f"기간: {data['start_at_display']} ~ {data['end_at_display']}",
        f"링크: {request.url_root.rstrip('/')}{url_for('view_poll', poll_id=poll_id)}",
    ]
    if poll['description']:
        lines.append(f"설명: {poll['description']}")
    return '\n'.join(lines)


def format_final_message(poll_id: int) -> str:
    data = build_poll_view_model(poll_id)
    poll = data['poll']

    lines = [
        '📊 투표 종료',
        f"제목: {poll['title']}",
        f"작성자: {poll['creator_nickname']}",
        f"기간: {data['start_at_display']} ~ {data['end_at_display']}",
        f"총 투표 수: {data['total_votes']}표",
        '',
        '투표 결과',
    ]

    for option in data['options']:
        lines.append(f"- {option['text']} ({option['count']}표)")
        if option['votes']:
            for vote in option['votes']:
                lines.append(f"  · {vote['nickname']}")
        else:
            lines.append('  · 없음')

    if data['comments']:
        lines.append('')
        lines.append('댓글')
        for comment in data['comments'][:20]:
            lines.append(f"- {comment['nickname']}: {comment['content']}")

    return '\n'.join(lines)


def send_webhook_message(content: str):
    webhook_url = app.config['WEBHOOK_URL']
    if not webhook_url:
        return False, '웹훅 URL 미설정'

    response = requests.post(webhook_url, json={'content': content}, timeout=15)
    response.raise_for_status()
    return True, 'sent'


def finalize_due_polls(force: bool = False, poll_ids=None):
    db = get_db()
    now_iso = kst_now().astimezone(UTC).isoformat()

    if poll_ids:
        placeholders = ','.join('?' for _ in poll_ids)
        polls = db.execute(
            f"SELECT * FROM polls WHERE id IN ({placeholders}) AND result_sent = 0",
            tuple(poll_ids),
        ).fetchall()
    elif force:
        polls = db.execute(
            "SELECT * FROM polls WHERE status = 'open' AND result_sent = 0"
        ).fetchall()
    else:
        polls = db.execute(
            "SELECT * FROM polls WHERE status = 'open' AND result_sent = 0 AND end_at <= ?",
            (now_iso,),
        ).fetchall()

    sent_ids = []
    for poll in polls:
        message = format_final_message(poll['id'])
        send_webhook_message(message)
        db.execute(
            "UPDATE polls SET status = 'closed', result_sent = 1, result_sent_at = ? WHERE id = ?",
            (now_iso, poll['id']),
        )
        sent_ids.append(poll['id'])

    db.commit()
    return sent_ids


def require_admin_or_flash():
    admin_nickname = request.form.get('admin_nickname', '').strip()
    admin_code = request.form.get('admin_code', '').strip()

    if admin_nickname != app.config['ADMIN_NICKNAME']:
        flash('관리자 닉네임이 올바르지 않아.', 'error')
        return None

    if admin_code not in app.config['ADMIN_CODES']:
        flash('관리자 코드가 올바르지 않아.', 'error')
        return None

    return True


@app.before_request
def ensure_db_exists():
    if not os.path.exists(app.config['DATABASE']):
        init_db()


@app.route('/')
def index():
    db = get_db()
    finalize_due_polls(force=False)

    all_open = db.execute(
        "SELECT * FROM polls WHERE status = 'open' ORDER BY start_at ASC, end_at ASC, id DESC"
    ).fetchall()
    active_polls = []
    scheduled_polls = []
    for poll in all_open:
        state = poll_state(poll)
        if state == 'scheduled':
            scheduled_polls.append(poll)
        elif state == 'open':
            active_polls.append(poll)

    closed_polls = db.execute(
        "SELECT * FROM polls WHERE status = 'closed' ORDER BY end_at DESC, id DESC LIMIT 20"
    ).fetchall()
    return render_template(
        'index.html',
        open_polls=active_polls,
        scheduled_polls=scheduled_polls,
        closed_polls=closed_polls,
        to_display=to_display,
    )


@app.route('/polls/new', methods=['GET', 'POST'])
def create_poll():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        creator_nickname = request.form.get('creator_nickname', '').strip() or app.config['ADMIN_NICKNAME']
        options_text = request.form.get('options_text', '').strip()
        description = request.form.get('description', '').strip()
        start_at = request.form.get('start_at', '').strip()
        end_at = request.form.get('end_at', '').strip()
        show_live_result = 1 if request.form.get('show_live_result') == 'on' else 0
        show_voter_names = 1 if request.form.get('show_voter_names') == 'on' else 0

        options = normalize_multiline_options(options_text)
        if not title:
            flash('투표 제목을 입력해줘.', 'error')
            return render_template('create_poll.html')
        if len(options) < 2:
            flash('투표 항목은 최소 2개 이상 필요해.', 'error')
            return render_template('create_poll.html')
        if not start_at or not end_at:
            flash('시작 시간과 종료 시간을 모두 입력해줘.', 'error')
            return render_template('create_poll.html')

        start_dt = to_kst(start_at)
        end_dt = to_kst(end_at)
        if end_dt <= start_dt:
            flash('종료 시간은 시작 시간보다 뒤여야 해.', 'error')
            return render_template('create_poll.html')

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO polls (
                title, description, creator_nickname, start_at, end_at,
                allow_duplicate, show_live_result, show_voter_names, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                title,
                description,
                creator_nickname,
                start_dt.astimezone(UTC).isoformat(),
                end_dt.astimezone(UTC).isoformat(),
                1,
                show_live_result,
                show_voter_names,
            ),
        )
        poll_id = cursor.lastrowid
        for idx, option in enumerate(options, start=1):
            db.execute(
                'INSERT INTO poll_options (poll_id, option_text, display_order) VALUES (?, ?, ?)',
                (poll_id, option, idx),
            )
        db.commit()

        try:
            send_webhook_message(format_created_message(poll_id))
        except Exception as exc:
            flash(f'투표는 생성됐지만 디스코드 알림 전송은 실패했어: {exc}', 'error')
        else:
            flash('투표가 생성됐고 디스코드에도 알렸어.', 'success')

        return redirect(url_for('view_poll', poll_id=poll_id))

    return render_template('create_poll.html')


@app.route('/polls/<int:poll_id>')
def view_poll(poll_id: int):
    finalize_due_polls(force=False)
    data = build_poll_view_model(poll_id)
    if data['is_scheduled']:
        flash(f"이 투표는 아직 시작 전이야. 시작 시간: {data['start_at_display']}", 'error')
    return render_template('poll_detail.html', **data)


@app.route('/polls/<int:poll_id>/vote', methods=['POST'])
def submit_vote(poll_id: int):
    poll = fetch_poll_or_404(poll_id)
    state = poll_state(poll)
    if state == 'closed':
        flash('이미 종료된 투표야.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    if state == 'scheduled':
        flash('아직 시작되지 않은 투표야.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    nickname = request.form.get('nickname', '').strip()
    option_ids = request.form.getlist('option_ids')

    if not nickname:
        flash('닉네임을 입력해줘.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))
    if not option_ids:
        flash('최소 1개 항목은 선택해야 해.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    db = get_db()
    valid_options = {
        str(row['id'])
        for row in db.execute('SELECT id FROM poll_options WHERE poll_id = ?', (poll_id,)).fetchall()
    }

    inserted = 0
    for option_id in option_ids:
        if option_id not in valid_options:
            continue

        existing = db.execute(
            'SELECT 1 FROM votes WHERE poll_id = ? AND nickname = ? AND option_id = ?',
            (poll_id, nickname, int(option_id)),
        ).fetchone()
        if existing:
            continue

        db.execute(
            'INSERT INTO votes (poll_id, option_id, nickname) VALUES (?, ?, ?)',
            (poll_id, int(option_id), nickname),
        )
        inserted += 1

    db.commit()

    if inserted == 0:
        flash('선택한 항목에는 이미 전부 투표한 상태야.', 'error')
    else:
        flash(f'{inserted}개 항목에 투표 완료!', 'success')

    return redirect(url_for('view_poll', poll_id=poll_id))


@app.route('/polls/<int:poll_id>/comments', methods=['POST'])
def submit_comment(poll_id: int):
    fetch_poll_or_404(poll_id)

    nickname = request.form.get('nickname', '').strip()
    content = request.form.get('content', '').strip()
    if not nickname or not content:
        flash('닉네임과 댓글 내용을 모두 입력해줘.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    db = get_db()
    db.execute(
        'INSERT INTO comments (poll_id, nickname, content) VALUES (?, ?, ?)',
        (poll_id, nickname, content),
    )
    db.commit()
    flash('댓글이 등록됐어.', 'success')
    return redirect(url_for('view_poll', poll_id=poll_id))


@app.route('/polls/<int:poll_id>/close', methods=['POST'])
def close_poll(poll_id: int):
    poll = fetch_poll_or_404(poll_id)
    if require_admin_or_flash() is None:
        return redirect(url_for('view_poll', poll_id=poll_id))

    if poll_state(poll) == 'closed':
        flash('이미 종료된 투표야.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    db = get_db()
    now_iso = kst_now().astimezone(UTC).isoformat()
    db.execute("UPDATE polls SET end_at = ?, status = 'open', result_sent = 0 WHERE id = ?", (now_iso, poll_id))
    db.commit()

    try:
        finalize_due_polls(force=False, poll_ids=[poll_id])
    except Exception as exc:
        flash(f'투표는 종료됐지만 디스코드 전송은 실패했어: {exc}', 'error')
    else:
        flash('투표를 종료했고 결과도 디스코드로 전송했어.', 'success')

    return redirect(url_for('view_poll', poll_id=poll_id))


@app.route('/polls/<int:poll_id>/delete', methods=['POST'])
def delete_poll(poll_id: int):
    poll = fetch_poll_or_404(poll_id)
    if require_admin_or_flash() is None:
        return redirect(url_for('view_poll', poll_id=poll_id))

    db = get_db()
    title = poll['title']
    db.execute('DELETE FROM polls WHERE id = ?', (poll_id,))
    db.commit()
    flash(f'"{title}" 투표를 삭제했어.', 'success')
    return redirect(url_for('index'))


@app.route('/internal/finalize')
def internal_finalize():
    token = request.args.get('token', '')
    if token != app.config['FINALIZE_TOKEN']:
        abort(403)

    sent_ids = finalize_due_polls(force=False)
    return {
        'ok': True,
        'sent_poll_ids': sent_ids,
        'count': len(sent_ids),
    }


@app.route('/healthz')
def healthz():
    return {'ok': True, 'time': kst_now().isoformat()}


if __name__ == '__main__':
    if not os.path.exists(app.config['DATABASE']):
        init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=True)
