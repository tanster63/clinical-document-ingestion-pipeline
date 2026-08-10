import pytest

from agent.tools import MAX_ROWS, SqlGuardError, guard_sql


def test_select_is_allowed_and_gets_a_limit(cfg):
    sql = guard_sql(f"SELECT * FROM `{cfg.dataset}.v_encounter_summary`", cfg)
    assert sql.rstrip().endswith(f"LIMIT {MAX_ROWS}")


def test_a_with_clause_is_allowed(cfg):
    sql = guard_sql(
        f"WITH x AS (SELECT mrn FROM `{cfg.dataset}.v_encounter_summary`) SELECT * FROM x",
        cfg,
    )
    assert "LIMIT" in sql


def test_an_existing_smaller_limit_is_left_alone(cfg):
    sql = guard_sql(f"SELECT mrn FROM `{cfg.dataset}.v_encounter_summary` LIMIT 5", cfg)
    assert sql.rstrip().endswith("LIMIT 5")


def test_an_oversized_limit_is_clamped(cfg):
    sql = guard_sql(f"SELECT mrn FROM `{cfg.dataset}.v_encounter_summary` LIMIT 99999", cfg)
    assert sql.rstrip().endswith(f"LIMIT {MAX_ROWS}")


@pytest.mark.parametrize("statement", [
    "DELETE FROM v_encounter_summary WHERE TRUE",
    "DROP TABLE encounters",
    "UPDATE patients SET mrn = '1'",
    "INSERT INTO patients (mrn) VALUES ('1')",
    "CREATE TABLE x AS SELECT 1",
    "TRUNCATE TABLE encounters",
    "MERGE patients T USING patients S ON TRUE WHEN MATCHED THEN DELETE",
    "GRANT `roles/bigquery.admin` ON SCHEMA ds TO 'user:x@y.com'",
])
def test_every_write_statement_is_refused(statement, cfg):
    with pytest.raises(SqlGuardError):
        guard_sql(statement, cfg)


def test_a_write_hidden_after_a_select_is_still_refused(cfg):
    with pytest.raises(SqlGuardError):
        guard_sql(
            f"SELECT mrn FROM `{cfg.dataset}.v_encounter_summary` "
            f"UNION ALL SELECT 1; DROP TABLE patients",
            cfg,
        )


def test_stacked_statements_are_refused(cfg):
    with pytest.raises(SqlGuardError):
        guard_sql(f"SELECT 1 FROM `{cfg.dataset}.v_encounter_summary`; "
                  f"DROP TABLE patients", cfg)


def test_a_semicolon_inside_a_string_literal_is_not_a_second_statement(cfg):
    sql = guard_sql(
        f"SELECT mrn FROM `{cfg.dataset}.v_encounter_summary` WHERE mrn LIKE '%; %'", cfg
    )
    assert "LIMIT" in sql


def test_queries_against_another_dataset_are_refused(cfg):
    with pytest.raises(SqlGuardError, match="dataset"):
        guard_sql("SELECT * FROM `other_project.other_ds.patients`", cfg)


def test_raw_tables_are_refused_the_agent_reads_views(cfg):
    with pytest.raises(SqlGuardError, match="view"):
        guard_sql(f"SELECT * FROM `{cfg.dataset}.patients`", cfg)


def test_both_views_are_reachable(cfg):
    for view in ("v_encounter_summary", "v_patient_timeline"):
        assert guard_sql(f"SELECT * FROM `{cfg.dataset}.{view}`", cfg)


def test_unnesting_a_views_own_array_column_is_allowed(cfg):
    """The timeline view nests diagnoses and prescriptions; refusing UNNEST
    would make half the warehouse unreachable through the guard."""
    sql = guard_sql(
        f"SELECT rx.drug_name FROM `{cfg.dataset}.v_patient_timeline` t, "
        f"UNNEST(t.prescriptions_written) rx",
        cfg,
    )
    assert "LIMIT" in sql


def test_a_query_with_no_table_reference_is_refused(cfg):
    with pytest.raises(SqlGuardError):
        guard_sql("SELECT 1", cfg)


def test_an_empty_query_is_refused(cfg):
    with pytest.raises(SqlGuardError):
        guard_sql("   ", cfg)


def test_the_guard_runs_before_bigquery_is_touched(cfg, monkeypatch):
    """run_sql must refuse without constructing a client, or a refusal still
    costs a credential lookup and a network round trip."""
    import agent.tools as tools

    def explode(**_):
        raise AssertionError("BigQuery client built for a query that must be refused")

    monkeypatch.setattr(tools, "load_config", lambda: cfg)
    monkeypatch.setattr(tools.bigquery, "Client", explode)
    assert tools.run_sql("DROP TABLE patients")["status"] == "refused"
    assert tools.run_sql(f"SELECT * FROM `{cfg.dataset}.patients`")["status"] == "refused"
