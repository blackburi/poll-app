import os
import psycopg
from psycopg.rows import dict_row
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler

import requests
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
KST = ZoneInfo('Asia/Seoul')
UTC = timezone.utc
ADMIN_SESSION_MINUTES = 5

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')
app.config['FINALIZE_TOKEN'] = os.getenv('FINALIZE_TOKEN', 'change-me-finalize-token')
app.config['WEBHOOK_URL'] = os.getenv('DISCORD_WEBHOOK_URL', '')
app.config['ADMIN_NICKNAME'] = os.getenv('ADMIN_NICKNAME', '관리자')
app.config['ADMIN_CODES'] = [
    code.strip() for code in os.getenv('ADMIN_CODES', '').split(',') if code.strip()
]


def get_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았어.")
    return database_url


def get_db():
    if 'db' not in g:
        g.db = psycopg.connect(get_database_url(), row_factory=dict_row)
    return g.db


@app.before_request
def ensure_db():
    db = get_db()
    try:
        db.execute("SELECT 1 FROM polls LIMIT 1")
    except Exception:
        schema_path = os.path.join(BASE_DIR, 'schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        with db.cursor() as cur:
            cur.execute(schema_sql)
        db.commit()


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    with psycopg.connect(get_database_url(), row_factory=dict_row) as db:
        with db.cursor() as cur:
            with open(os.path.join(BASE_DIR, 'schema.sql'), 'r', encoding='utf-8') as f:
                cur.execute(f.read())
        db.commit()


def kst_now():
    return datetime.now(KST)


def now_utc_iso():
    return datetime.now(UTC).isoformat()


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
    poll = db.execute('SELECT * FROM polls WHERE id = %s', (poll_id,)).fetchone()
    if poll is None:
        abort(404)
    return poll


def fetch_poll_detail(poll_id: int):
    db = get_db()
    poll = fetch_poll_or_404(poll_id)
    options = db.execute(
        '''
        SELECT *
        FROM poll_options
        WHERE poll_id = %s
        ORDER BY id
        ''',
        (poll_id,),
    ).fetchall()
    votes = db.execute(
        '''
        SELECT v.*, o.option_text
        FROM votes v
        JOIN poll_options o ON o.id = v.option_id
        WHERE v.poll_id = %s
        ORDER BY v.created_at ASC, v.id ASC
        ''',
        (poll_id,),
    ).fetchall()

    comments = []
    try:
        comments = db.execute(
            '''
            SELECT *
            FROM comments
            WHERE poll_id = %s
            ORDER BY created_at DESC, id DESC
            ''',
            (poll_id,),
        ).fetchall()
    except Exception:
        comments = []

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


def validate_admin_code(admin_code: str):
    if admin_code not in app.config['ADMIN_CODES']:
        return False, '잘못된 비밀번호가 입력되었어.'
    return True, 'ok'


def grant_admin_access(scope: str):
    expires_at = datetime.now(UTC) + timedelta(minutes=ADMIN_SESSION_MINUTES)
    session[scope] = expires_at.isoformat()


def has_admin_access(scope: str):
    expires_at = session.get(scope)
    if not expires_at:
        return False
    try:
        expires_dt = from_iso(expires_at)
    except ValueError:
        session.pop(scope, None)
        return False
    if expires_dt <= datetime.now(UTC):
        session.pop(scope, None)
        return False
    return True


def ensure_admin_access(scope: str, fail_redirect: str, **kwargs):
    if has_admin_access(scope):
        return True
    flash('관리자 인증이 필요해. 버튼을 다시 눌러서 비밀번호를 입력해줘.', 'error')
    return redirect(url_for(fail_redirect, **kwargs))


def format_created_message(poll_id: int) -> str:
    data = build_poll_view_model(poll_id)
    poll = data['poll']
    lines = [
        '🆕 새 투표가 생성되었습니다.',
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
                if vote['nickname'] == vote['representative_nickname']:
                    lines.append(f"  · {vote['nickname']}")
                else:
                    lines.append(f"  · {vote['nickname']} (대표: {vote['representative_nickname']})")
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
        placeholders = ','.join(['%s'] * len(poll_ids))
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
            "SELECT * FROM polls WHERE status = 'open' AND result_sent = 0 AND end_at::timestamptz <= %s",
            (now_iso,),
        ).fetchall()

    sent_ids = []
    for poll in polls:
        message = format_final_message(poll['id'])
        send_webhook_message(message)
        db.execute(
            "UPDATE polls SET status = 'closed', result_sent = 1, result_sent_at = %s WHERE id = %s",
            (now_iso, poll['id']),
        )
        sent_ids.append(poll['id'])

    db.commit()
    return sent_ids


def run_finalize_due_polls():
    with app.app_context():
        finalize_due_polls(force=False)


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=run_finalize_due_polls,
        trigger="interval",
        seconds=60,
        id="finalize_polls",
        replace_existing=True
    )
    scheduler.start()


def render_create_poll(form_data=None):
    form_data = form_data or {}
    return render_template('create_poll.html', form_data=form_data)


def render_edit_poll(poll_id: int, form_data=None):
    poll = fetch_poll_or_404(poll_id)
    db = get_db()
    option_rows = db.execute(
        'SELECT id, option_text FROM poll_options WHERE poll_id = %s ORDER BY id',
        (poll_id,),
    ).fetchall()

    prepared_option_rows = []
    if form_data and form_data.get('option_rows'):
        prepared_option_rows = form_data['option_rows']
    else:
        prepared_option_rows = [{'id': row['id'], 'text': row['option_text']} for row in option_rows]

    return render_template(
        'edit_poll.html',
        poll=poll,
        form_data=form_data or {
            'title': poll['title'],
            'description': poll['description'] or '',
            'option_rows': prepared_option_rows,
        },
    )


@app.route('/admin/verify', methods=['POST'])
def verify_admin():
    admin_code = request.form.get('admin_code', '').strip()
    action = request.form.get('action', '').strip()
    poll_id = request.form.get('poll_id', '').strip()

    ok, message = validate_admin_code(admin_code)
    if not ok:
        return jsonify({'ok': False, 'message': message}), 200

    if action == 'create':
        grant_admin_access('admin_create')
        return jsonify({'ok': True, 'redirect_url': url_for('create_poll')})

    if action == 'edit':
        if not poll_id.isdigit():
            return jsonify({'ok': False, 'message': '수정할 투표 정보가 올바르지 않아.'}), 400
        fetch_poll_or_404(int(poll_id))
        grant_admin_access(f'admin_edit_{poll_id}')
        return jsonify({'ok': True, 'redirect_url': url_for('edit_poll', poll_id=int(poll_id))})

    if action in {'close', 'delete'}:
        if not poll_id.isdigit():
            return jsonify({'ok': False, 'message': '대상 투표 정보가 올바르지 않아.'}), 400
        fetch_poll_or_404(int(poll_id))
        return jsonify({'ok': True, 'message': '인증이 완료됐어.'})

    return jsonify({'ok': False, 'message': '지원하지 않는 관리자 작업이야.'}), 400


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
    auth = ensure_admin_access('admin_create', 'index')
    if auth is not True:
        return auth

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        creator_nickname = request.form.get('creator_nickname', '').strip() or app.config['ADMIN_NICKNAME']
        options_text = request.form.get('options_text', '').strip()
        description = request.form.get('description', '').strip()
        start_at = request.form.get('start_at', '').strip()
        end_at = request.form.get('end_at', '').strip()

        form_data = {
            'title': title,
            'creator_nickname': creator_nickname,
            'options_text': options_text,
            'description': description,
            'start_at': start_at,
            'end_at': end_at,
        }

        options = normalize_multiline_options(options_text)
        if not title:
            flash('투표 제목을 입력해줘.', 'error')
            return render_create_poll(form_data)
        if len(options) < 2:
            flash('투표 항목은 최소 2개 이상 필요해.', 'error')
            return render_create_poll(form_data)
        if not start_at or not end_at:
            flash('시작 시간과 종료 시간을 모두 입력해줘.', 'error')
            return render_create_poll(form_data)

        try:
            start_dt = to_kst(start_at)
            end_dt = to_kst(end_at)
        except ValueError:
            flash('시간 형식이 올바르지 않아.', 'error')
            return render_create_poll(form_data)

        if end_dt <= start_dt:
            flash('종료 시간은 시작 시간보다 뒤여야 해.', 'error')
            return render_create_poll(form_data)

        db = get_db()
        cursor = db.execute(
            '''
            INSERT INTO polls (
                title, description, creator_nickname, start_at, end_at,
                status, result_sent, result_sent_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, 'open', 0, NULL, %s)
            RETURNING id
            ''',
            (
                title,
                description,
                creator_nickname,
                start_dt.astimezone(UTC).isoformat(),
                end_dt.astimezone(UTC).isoformat(),
                now_utc_iso(),
            ),
        )
        poll_id = cursor.fetchone()['id']

        created_at = now_utc_iso()
        for option in options:
            db.execute(
                'INSERT INTO poll_options (poll_id, option_text, created_at) VALUES (%s, %s, %s)',
                (poll_id, option, created_at),
            )

        db.commit()
        session.pop('admin_create', None)

        try:
            send_webhook_message(format_created_message(poll_id))
        except Exception as exc:
            flash(f'투표는 생성됐지만 디스코드 알림 전송은 실패했어: {exc}', 'error')
        else:
            flash('투표가 생성됐고 디스코드에도 알렸어.', 'success')

        return redirect(url_for('view_poll', poll_id=poll_id))

    return render_create_poll()


@app.route('/polls/<int:poll_id>/edit', methods=['GET', 'POST'])
def edit_poll(poll_id: int):
    auth = ensure_admin_access(f'admin_edit_{poll_id}', 'view_poll', poll_id=poll_id)
    if auth is not True:
        return auth

    fetch_poll_or_404(poll_id)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()

        option_ids = request.form.getlist('option_id')
        option_texts = request.form.getlist('option_text')

        option_rows = []
        normalized_seen = set()
        normalized_count = 0
        for raw_id, raw_text in zip(option_ids, option_texts):
            cleaned_text = raw_text.strip()
            option_id = raw_id.strip()
            option_rows.append({'id': option_id, 'text': cleaned_text})

            if cleaned_text:
                if cleaned_text in normalized_seen:
                    flash(f'"{cleaned_text}" 항목이 중복되었어.', 'error')
                    return render_edit_poll(
                        poll_id,
                        {'title': title, 'description': description, 'option_rows': option_rows},
                    )
                normalized_seen.add(cleaned_text)
                normalized_count += 1

        if not title:
            flash('투표 제목을 입력해줘.', 'error')
            return render_edit_poll(
                poll_id,
                {'title': title, 'description': description, 'option_rows': option_rows},
            )

        if normalized_count < 2:
            flash('투표 항목은 최소 2개 이상 남아 있어야 해.', 'error')
            return render_edit_poll(
                poll_id,
                {'title': title, 'description': description, 'option_rows': option_rows},
            )

        db = get_db()
        existing_rows = db.execute(
            'SELECT id FROM poll_options WHERE poll_id = %s ORDER BY id',
            (poll_id,),
        ).fetchall()
        existing_ids = {str(row['id']) for row in existing_rows}
        delete_option_ids = []

        for raw_id, raw_text in zip(option_ids, option_texts):
            option_id = raw_id.strip()
            cleaned_text = raw_text.strip()

            if option_id and option_id not in existing_ids:
                continue

            if option_id and cleaned_text:
                db.execute(
                    'UPDATE poll_options SET option_text = %s WHERE id = %s AND poll_id = %s',
                    (cleaned_text, int(option_id), poll_id),
                )
            elif option_id and not cleaned_text:
                delete_option_ids.append(int(option_id))
            elif not option_id and cleaned_text:
                db.execute(
                    'INSERT INTO poll_options (poll_id, option_text, created_at) VALUES (%s, %s, %s)',
                    (poll_id, cleaned_text, now_utc_iso()),
                )

        if delete_option_ids:
            placeholders = ','.join(['%s'] * len(delete_option_ids))
            db.execute(
                f'DELETE FROM votes WHERE poll_id = %s AND option_id IN ({placeholders})',
                (poll_id, *delete_option_ids),
            )
            db.execute(
                f'DELETE FROM poll_options WHERE poll_id = %s AND id IN ({placeholders})',
                (poll_id, *delete_option_ids),
            )

        db.execute(
            'UPDATE polls SET title = %s, description = %s WHERE id = %s',
            (title, description, poll_id),
        )
        db.commit()
        session.pop(f'admin_edit_{poll_id}', None)

        if delete_option_ids:
            flash('투표를 수정했어. 삭제된 항목에 있던 기존 투표는 함께 제거됐어.', 'success')
        else:
            flash('투표를 수정했어.', 'success')

        return redirect(url_for('view_poll', poll_id=poll_id))

    return render_edit_poll(poll_id)


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
    representative_nickname = request.form.get('representative_nickname', '').strip()
    option_ids = [value.strip() for value in request.form.getlist('option_id') if value.strip()]

    if not nickname:
        flash('닉네임을 입력해줘.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))
    if not representative_nickname:
        flash('대표 캐릭터 닉네임을 입력해줘.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))
    if not option_ids:
        flash('투표 항목을 하나 이상 선택해줘.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    option_ids = list(dict.fromkeys(option_ids))
    db = get_db()

    placeholders = ','.join(['%s'] * len(option_ids))
    valid_rows = db.execute(
        f'''
        SELECT id
        FROM poll_options
        WHERE poll_id = %s AND id IN ({placeholders})
        ''',
        (poll_id, *option_ids),
    ).fetchall()

    valid_option_ids = {str(row['id']) for row in valid_rows}
    if len(valid_option_ids) != len(option_ids):
        flash('유효하지 않은 투표 항목이 포함되어 있어.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    db.execute(
        'DELETE FROM votes WHERE poll_id = %s AND representative_nickname = %s',
        (poll_id, representative_nickname),
    )

    created_at = now_utc_iso()
    for option_id in option_ids:
        db.execute(
            '''
            INSERT INTO votes (poll_id, option_id, nickname, representative_nickname, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ''',
            (poll_id, int(option_id), nickname, representative_nickname, created_at),
        )

    db.commit()

    flash('선택한 여러 항목으로 투표가 저장됐어. 기존 투표가 있었다면 새 선택으로 갱신됐어.', 'success')
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
    try:
        db.execute(
            'INSERT INTO comments (poll_id, nickname, content, created_at) VALUES (%s, %s, %s, %s)',
            (poll_id, nickname, content, now_utc_iso()),
        )
        db.commit()
    except Exception:
        flash('댓글 기능이 현재 비활성화되어 있어.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    flash('댓글이 등록됐어.', 'success')
    return redirect(url_for('view_poll', poll_id=poll_id))


@app.route('/polls/<int:poll_id>/close', methods=['POST'])
def close_poll(poll_id: int):
    poll = fetch_poll_or_404(poll_id)
    admin_code = request.form.get('admin_code', '').strip()
    ok, message = validate_admin_code(admin_code)
    if not ok:
        flash(message, 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    if poll_state(poll) == 'closed':
        flash('이미 종료된 투표야.', 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    db = get_db()
    now_iso = kst_now().astimezone(UTC).isoformat()
    db.execute(
        "UPDATE polls SET end_at = %s, status = 'open', result_sent = 0 WHERE id = %s",
        (now_iso, poll_id),
    )
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
    admin_code = request.form.get('admin_code', '').strip()
    ok, message = validate_admin_code(admin_code)
    if not ok:
        flash(message, 'error')
        return redirect(url_for('view_poll', poll_id=poll_id))

    db = get_db()
    title = poll['title']
    db.execute('DELETE FROM polls WHERE id = %s', (poll_id,))
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


def should_start_scheduler():
    return os.environ.get("RENDER") == "true" or os.environ.get("FLASK_ENV") != "development"


if should_start_scheduler():
    start_scheduler()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False)
