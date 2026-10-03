"""Risk propagation over real commit co-change evidence."""
from collections import defaultdict
from itertools import combinations


MIN_COOCCURRENCE = 2
MAX_HOPS = 3
HOP_DECAY = 0.6


def cochange_evidence(commits, module_by_repo_path):
    """Count commits containing each real pair of analyzed modules."""
    evidence = defaultdict(lambda: {"commit_ids": [], "count": 0})
    commits_by_repo = defaultdict(list)
    for commit in commits:
        commits_by_repo[commit.repository_id].append(commit)
    recent_ids = set()
    for repository_commits in commits_by_repo.values():
        ordered_commits = sorted(
            repository_commits,
            key=lambda commit: (commit.committed_at or commit.created_at).isoformat()
            if commit.committed_at or commit.created_at else "",
            reverse=True,
        )
        recent_ids.update(commit.id for commit in ordered_commits[:12])
    recent_counts = defaultdict(int)

    for commit in commits:
        module_ids = sorted({
            module_id
            for path in (commit.files or [])
            for module_id in module_by_repo_path.get((commit.repository_id, path), [])
        })
        for left_id, right_id in combinations(module_ids, 2):
            pair = (left_id, right_id)
            evidence[pair]["count"] += 1
            evidence[pair]["commit_ids"].append(commit.id)
            if commit.id in recent_ids:
                recent_counts[pair] += 1

    for pair, value in evidence.items():
        value["recent_count"] = recent_counts[pair]
    return dict(evidence)


def propagate_risk(seed_id, seed_risk, evidence, minimum=MIN_COOCCURRENCE,
                   max_hops=MAX_HOPS, decay=HOP_DECAY):
    """Return max-path risk contributions using only thresholded real edges."""
    if seed_risk is None or seed_risk <= 0.5:
        return []

    adjacency = defaultdict(list)
    for (left_id, right_id), edge in evidence.items():
        if edge["count"] < minimum:
            continue
        adjacency[left_id].append((right_id, edge))
        adjacency[right_id].append((left_id, edge))

    best = {}
    frontier = [(seed_id, seed_risk, 0, (seed_id,))]
    while frontier:
        current_id, current_risk, hops, path = frontier.pop(0)
        if hops >= max_hops:
            continue
        for neighbor_id, edge in adjacency[current_id]:
            if neighbor_id in path:
                continue
            edge_strength = edge["count"] / (edge["count"] + minimum)
            contribution = current_risk * edge_strength * decay
            previous = best.get(neighbor_id)
            if previous and previous["propagated_risk_contribution"] >= contribution:
                continue
            result = {
                "module_id": neighbor_id,
                "propagated_risk_contribution": round(contribution, 4),
                "cochange_count": edge["count"],
                "recent_joint_commits": edge["recent_count"],
                "commit_ids": edge["commit_ids"],
                "hops": hops + 1,
                "path": list(path) + [neighbor_id],
            }
            best[neighbor_id] = result
            frontier.append((neighbor_id, contribution, hops + 1, tuple(result["path"])))

    return sorted(
        best.values(),
        key=lambda item: item["propagated_risk_contribution"],
        reverse=True,
    )