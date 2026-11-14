"""Create the initial CineContext tables."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE films (
	slug VARCHAR NOT NULL,
	title VARCHAR NOT NULL,
	original_title VARCHAR NOT NULL,
	tmdb_id INTEGER,
	imdb_id VARCHAR,
	facts JSONB NOT NULL,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (slug),
	UNIQUE (tmdb_id),
	UNIQUE (imdb_id)
)
    """)
    op.execute("""
CREATE TABLE runs (
	submission_key UUID NOT NULL,
	constraints JSONB NOT NULL,
	model_mode VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	stage VARCHAR NOT NULL,
	progress JSONB NOT NULL,
	study_result JSONB,
	guide JSONB,
	error TEXT,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (submission_key)
)
    """)
    op.execute("""
CREATE TABLE sources (
	url TEXT NOT NULL,
	title TEXT NOT NULL,
	usage_note TEXT NOT NULL,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (url)
)
    """)
    op.execute("""
CREATE TABLE film_releases (
	film_id UUID NOT NULL,
	label VARCHAR NOT NULL,
	year INTEGER NOT NULL,
	region VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	source_url TEXT NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (film_id, label, region, kind),
	FOREIGN KEY(film_id) REFERENCES films (id)
)
    """)
    op.execute("""
CREATE TABLE passages (
	source_id UUID NOT NULL,
	locator VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	is_current BOOLEAN NOT NULL,
	body TEXT NOT NULL,
	search_terms TEXT NOT NULL,
	kind VARCHAR NOT NULL,
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, body || ' ' || search_terms)) STORED NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (source_id, locator, revision),
	FOREIGN KEY(source_id) REFERENCES sources (id)
)
    """)
    op.execute("""
CREATE UNIQUE INDEX ix_passages_current ON passages (source_id, locator) WHERE is_current
    """)
    op.execute("""
CREATE INDEX ix_passages_search ON passages USING gin (search_vector)
    """)
    op.execute("""
CREATE TABLE rating_snapshots (
	film_id UUID NOT NULL,
	origin VARCHAR NOT NULL,
	metric VARCHAR NOT NULL,
	payload JSONB NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (film_id, origin, metric),
	FOREIGN KEY(film_id) REFERENCES films (id)
)
    """)
    op.execute("""
CREATE TABLE run_films (
	run_id UUID NOT NULL,
	film_id UUID NOT NULL,
	decision VARCHAR NOT NULL,
	explanation JSONB NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id, film_id),
	FOREIGN KEY(run_id) REFERENCES runs (id),
	FOREIGN KEY(film_id) REFERENCES films (id)
)
    """)
    op.execute("""
CREATE INDEX ix_run_films_run_id ON run_films (run_id)
    """)
    op.execute("""
CREATE TABLE tool_calls (
	run_id UUID NOT NULL,
	operation VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	elapsed_ms INTEGER NOT NULL,
	detail JSONB NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES runs (id)
)
    """)
    op.execute("""
CREATE INDEX ix_tool_calls_run_id ON tool_calls (run_id)
    """)
    op.execute("""
CREATE TABLE film_passages (
	film_id UUID NOT NULL,
	passage_id UUID NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (film_id, passage_id),
	FOREIGN KEY(film_id) REFERENCES films (id),
	FOREIGN KEY(passage_id) REFERENCES passages (id)
)
    """)
    op.execute("""
CREATE INDEX ix_film_passages_film_id ON film_passages (film_id)
    """)
    op.execute("""
CREATE TABLE run_evidence (
	run_id UUID NOT NULL,
	film_id UUID,
	passage_id UUID,
	source_title TEXT NOT NULL,
	source_url TEXT NOT NULL,
	locator TEXT NOT NULL,
	body TEXT NOT NULL,
	kind VARCHAR NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id, id),
	FOREIGN KEY(run_id) REFERENCES runs (id),
	FOREIGN KEY(film_id) REFERENCES films (id),
	FOREIGN KEY(passage_id) REFERENCES passages (id)
)
    """)
    op.execute("""
CREATE INDEX ix_run_evidence_run_id ON run_evidence (run_id)
    """)
    op.execute("""
CREATE TABLE timeline_events (
	run_id UUID NOT NULL,
	event_key VARCHAR NOT NULL,
	axis VARCHAR NOT NULL,
	evidence_id UUID NOT NULL,
	data JSONB NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id, id),
	UNIQUE (run_id, event_key),
	FOREIGN KEY(run_id, evidence_id) REFERENCES run_evidence (run_id, id),
	FOREIGN KEY(run_id) REFERENCES runs (id)
)
    """)
    op.execute("""
CREATE INDEX ix_timeline_events_run_id ON timeline_events (run_id)
    """)
    op.execute("""
CREATE TABLE film_events (
	run_id UUID NOT NULL,
	film_id UUID NOT NULL,
	event_id UUID NOT NULL,
	evidence_id UUID NOT NULL,
	relation VARCHAR NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id, film_id, event_id),
	FOREIGN KEY(run_id, film_id) REFERENCES run_films (run_id, film_id),
	FOREIGN KEY(run_id, event_id) REFERENCES timeline_events (run_id, id),
	FOREIGN KEY(run_id, evidence_id) REFERENCES run_evidence (run_id, id)
)
    """)
    op.execute("""
CREATE TABLE timeline_edges (
	run_id UUID NOT NULL,
	earlier_id UUID NOT NULL,
	later_id UUID NOT NULL,
	evidence_id UUID NOT NULL,
	id UUID NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id, earlier_id, later_id),
	FOREIGN KEY(run_id, earlier_id) REFERENCES timeline_events (run_id, id),
	FOREIGN KEY(run_id, later_id) REFERENCES timeline_events (run_id, id),
	FOREIGN KEY(run_id, evidence_id) REFERENCES run_evidence (run_id, id)
)
    """)


def downgrade():
    op.drop_table("timeline_edges")
    op.drop_table("film_events")
    op.drop_table("timeline_events")
    op.drop_table("run_evidence")
    op.drop_table("film_passages")
    op.drop_table("tool_calls")
    op.drop_table("run_films")
    op.drop_table("rating_snapshots")
    op.drop_table("passages")
    op.drop_table("film_releases")
    op.drop_table("sources")
    op.drop_table("runs")
    op.drop_table("films")
