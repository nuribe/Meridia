"""Normalización de planes de ejecución (PostgreSQL y SQL Server)."""
from pg_diagrammer.domain.explain import (
    nodes_to_text,
    parse_mssql_plan,
    parse_postgres_plan,
)

PG_PLAN = [
    "Hash Join  (cost=1.09..2.23 rows=5 width=64) (actual time=0.020..0.030 rows=5 loops=1)",
    "  Hash Cond: (p.id_cliente = c.id)",
    "  ->  Seq Scan on paneles p  (cost=0.00..1.05 rows=5 width=32) (actual time=0.005..0.006 rows=5 loops=1)",
    "  ->  Hash  (cost=1.04..1.04 rows=4 width=36)",
    "        ->  Seq Scan on clientes c  (cost=0.00..1.04 rows=4 width=36)",
    "Planning Time: 0.180 ms",
    "Execution Time: 0.061 ms",
]


def test_postgres_tree_depths():
    nodes = parse_postgres_plan(PG_PLAN)
    ops = [(n["depth"], n["op"]) for n in nodes]
    assert ops[0] == (0, "Hash Join")
    assert ops[1] == (1, "Seq Scan on paneles p")
    assert ops[2] == (1, "Hash")
    assert ops[3] == (2, "Seq Scan on clientes c")


def test_postgres_costs_and_details():
    root = parse_postgres_plan(PG_PLAN)[0]
    assert root["estimate_rows"] == 5.0
    assert root["cost"] == 2.23
    assert root["actual_rows"] == 5.0
    assert root["actual_time"] == 0.030
    assert root["detail"] == ["Hash Cond: (p.id_cliente = c.id)"]


def test_postgres_summary_lines_are_marked_and_not_operators():
    """El diagrama solo dibuja `kind == "operator"`: Planning/Execution Time
    son resúmenes, no nodos del árbol del plan."""
    nodes = parse_postgres_plan(PG_PLAN)
    summary = [n for n in nodes if n["kind"] == "summary"]
    assert [n["op"] for n in summary] == ["Planning Time: 0.180 ms", "Execution Time: 0.061 ms"]
    assert all(n["cost"] is None for n in summary)
    assert all(n["kind"] == "operator" for n in nodes if n not in summary)


def test_mssql_root_row_is_labelled_with_its_verb():
    """La fila raíz de SHOWPLAN_ALL trae la sentencia entera; como nodo del
    diagrama basta el verbo, igual que el nodo SELECT de SSMS."""
    root = parse_mssql_plan(MSSQL_COLUMNS, MSSQL_ROWS)[0]
    assert root["op"] == "SELECT"
    assert root["text"] == "SELECT * FROM [Paneles]"
    assert root["kind"] == "operator"


def test_postgres_estimated_plan_without_actual():
    nodes = parse_postgres_plan(
        ["Seq Scan on paneles  (cost=0.00..1.05 rows=5 width=32)"]
    )
    assert nodes[0]["actual_rows"] is None
    assert nodes[0]["estimate_rows"] == 5.0


MSSQL_COLUMNS = [
    "Rows", "Executes", "StmtText", "StmtId", "NodeId", "Parent",
    "PhysicalOp", "LogicalOp", "Argument", "EstimateRows", "TotalSubtreeCost",
]

MSSQL_ROWS = [
    (None, None, "SELECT * FROM [Paneles]", 1, 1, 0, None, None, None, 465.0, 0.31),
    (None, None, "  |--Nested Loops(Inner Join)", 1, 2, 1, "Nested Loops",
     "Inner Join", "OUTER REFERENCES:([Id])", 465.0, 0.31),
    (None, None, "       |--Clustered Index Scan(OBJECT:([Paneles]))", 1, 3, 2,
     "Clustered Index Scan", "Clustered Index Scan", "OBJECT:([PK_Paneles])", 465.0, 0.02),
]


def test_mssql_tree_depths_and_ops():
    nodes = parse_mssql_plan(MSSQL_COLUMNS, MSSQL_ROWS)
    assert [n["depth"] for n in nodes] == [0, 1, 2]
    assert nodes[1]["op"] == "Nested Loops"
    assert nodes[2]["op"] == "Clustered Index Scan"


def test_mssql_numbers_and_detail():
    nodes = parse_mssql_plan(MSSQL_COLUMNS, MSSQL_ROWS)
    assert nodes[1]["estimate_rows"] == 465.0
    assert nodes[1]["cost"] == 0.31
    assert nodes[1]["actual_rows"] is None  # SHOWPLAN_ALL no trae filas reales
    assert "Lógico: Inner Join" in nodes[1]["detail"]
    assert "OUTER REFERENCES:([Id])" in nodes[1]["detail"]


def test_mssql_statistics_profile_actual_rows():
    columns = ["Rows", "StmtText", "PhysicalOp", "EstimateRows", "TotalSubtreeCost"]
    rows = [(465, "  |--Index Scan", "Index Scan", 400.0, 0.02)]
    node = parse_mssql_plan(columns, rows)[0]
    assert node["actual_rows"] == 465.0
    assert node["estimate_rows"] == 400.0


def test_mssql_ignores_unknown_columns():
    node = parse_mssql_plan(["StmtText"], [("SELECT 1",)])[0]
    assert node["depth"] == 0
    assert node["cost"] is None


def test_nodes_to_text_indents_by_depth():
    text = nodes_to_text(parse_postgres_plan(PG_PLAN))
    lines = text.split("\n")
    assert lines[0].startswith("Hash Join")
    assert lines[1].strip().startswith("Hash Cond:")
    assert lines[2].startswith("  -> Seq Scan on paneles p")
