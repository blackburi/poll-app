CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    creator_nickname TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    result_sent INTEGER NOT NULL DEFAULT 0,
    result_sent_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL,
    option_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL,
    option_id INTEGER NOT NULL,
    nickname TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE,
    FOREIGN KEY (option_id) REFERENCES poll_options (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL,
    nickname TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE
);