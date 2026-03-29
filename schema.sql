CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    creator_nickname TEXT NOT NULL,
    end_at TIMESTAMPTZ,
    start_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    allow_duplicate INTEGER NOT NULL DEFAULT 1,
    show_live_result INTEGER NOT NULL DEFAULT 1,
    show_voter_names INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    result_sent INTEGER NOT NULL DEFAULT 0,
    result_sent_at TEXT
);

CREATE TABLE IF NOT EXISTS poll_options (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL,
    option_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE
);

DROP TABLE IF EXISTS votes;

CREATE TABLE IF NOT EXISTS votes (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL,
    option_id INTEGER NOT NULL,
    nickname TEXT NOT NULL,
    representative_nickname TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE,
    FOREIGN KEY (option_id) REFERENCES poll_options (id) ON DELETE CASCADE
);

DROP INDEX IF EXISTS idx_votes_poll_rep;

CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_poll_rep_option
ON votes (poll_id, representative_nickname, option_id);

CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL,
    nickname TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE
);