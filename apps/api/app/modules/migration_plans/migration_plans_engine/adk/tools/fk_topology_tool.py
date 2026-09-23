"""
Foreign Key Topology Tool for Google ADK Agents
Validates table foreign key dependency graphs and determines cycle-free migration execution order.
"""

from collections import defaultdict, deque
from typing import Any, Dict, List, Set, Tuple


def analyze_foreign_key_topology(
    table_names: List[str],
    relationships: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Given a list of table names and a list of relationships
    [{"from_table": "orders", "to_table": "customers"}],
    computes topological sort order and detects circular dependencies.
    """
    in_degree: Dict[str, int] = {t: 0 for t in table_names}
    adj: Dict[str, Set[str]] = defaultdict(set)

    # Build adjacency list: parent -> child (parent must be loaded before child)
    for rel in relationships:
        parent = rel.get("to_table")
        child = rel.get("from_table")

        if parent and child and parent != child:
            if parent in in_degree and child in in_degree:
                if child not in adj[parent]:
                    adj[parent].add(child)
                    in_degree[child] += 1

    # Kahn's algorithm
    queue = deque([t for t in table_names if in_degree[t] == 0])
    ordered_tables: List[str] = []

    while queue:
        curr = queue.popleft()
        ordered_tables.append(curr)

        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    has_cycles = len(ordered_tables) < len(table_names)
    cycle_tables = [t for t in table_names if in_degree[t] > 0] if has_cycles else []

    return {
        "has_cycles": has_cycles,
        "is_valid": not has_cycles,
        "recommended_load_order": ordered_tables if not has_cycles else table_names,
        "cycle_tables": cycle_tables,
        "warnings": (
            [f"Circular dependency detected between tables: {cycle_tables}. Defer foreign key constraints to post-migration phase."]
            if has_cycles else []
        ),
    }
