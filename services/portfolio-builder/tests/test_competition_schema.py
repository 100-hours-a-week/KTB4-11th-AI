import pytest
import sqlalchemy as sa


def test_competition_tables_accept_a_participant_a_job_and_a_shared_link(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO competition_participants"
                " (participant_id, competition_id, user_id, risk_profile,"
                " interested_theme_codes, excluded_theme_codes, additional_request)"
                " VALUES (1, 10, 100, '성장형', '{T1}', '{}', ''),"
                " (2, 10, 100, '성장형', '{T1}', '{}', '')"
            )
        )
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.1, 'c', 'm') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO competition_portfolios (participant_id, portfolio_id)"
                " VALUES (1, :p), (2, :p)"
            ),
            {"p": portfolio_id},
        )
        linked = conn.execute(
            sa.text("SELECT count(*) FROM competition_portfolios WHERE portfolio_id = :p"),
            {"p": portfolio_id},
        ).scalar_one()
    assert linked == 2


def test_generation_key_is_unique(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO competition_participants"
                " (participant_id, competition_id, user_id, risk_profile, additional_request)"
                " VALUES (1, 10, 100, '성장형', '')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO competition_jobs (generation_key, participant_id, status)"
                " VALUES ('k', 1, 'running')"
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO competition_jobs (generation_key, participant_id, status)"
                    " VALUES ('k', 1, 'running')"
                )
            )


def test_acknowledged_constraints_defaults_to_null(engine):
    with engine.begin() as conn:
        stored = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.1, 'c', 'm') RETURNING acknowledged_constraints"
            )
        ).scalar_one()
    assert stored is None


def test_competition_jobs_rejects_an_unknown_status(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO competition_participants"
                " (participant_id, competition_id, user_id, risk_profile, additional_request)"
                " VALUES (1, 10, 100, '성장형', '')"
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO competition_jobs (generation_key, participant_id, status)"
                    " VALUES ('k', 1, 'pending')"
                )
            )


def test_deleting_a_portfolio_clears_competition_jobs_references(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO competition_participants"
                " (participant_id, competition_id, user_id, risk_profile, additional_request)"
                " VALUES (1, 10, 100, '성장형', '')"
            )
        )
        portfolio_id, previous_portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.1, 'c', 'm'), (0.1, 'c', 'm') RETURNING id"
            )
        ).scalars()
        conn.execute(
            sa.text(
                "INSERT INTO competition_jobs"
                " (generation_key, participant_id, status, portfolio_id, previous_portfolio_id)"
                " VALUES ('k', 1, 'running', :portfolio_id, :previous_portfolio_id)"
            ),
            {"portfolio_id": portfolio_id, "previous_portfolio_id": previous_portfolio_id},
        )
        conn.execute(
            sa.text("DELETE FROM portfolios WHERE id IN (:portfolio_id, :previous_portfolio_id)"),
            {"portfolio_id": portfolio_id, "previous_portfolio_id": previous_portfolio_id},
        )
        row = conn.execute(
            sa.text(
                "SELECT portfolio_id, previous_portfolio_id FROM competition_jobs"
                " WHERE generation_key = 'k'"
            )
        ).one()
    assert row.portfolio_id is None
    assert row.previous_portfolio_id is None
