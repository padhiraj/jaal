"""Graph algorithms implemented from first principles (pure Python, no dependencies),
so every number the system shows can be reproduced and explained in court.

Graph format: adjacency dict {node: {neighbour: weight}} (undirected, symmetric).
"""
from __future__ import annotations

import math
from collections import deque


def tie_cost(w: float) -> float:
    """Distance of a tie: strong (frequent) ties are short, one-off contacts are long."""
    return 1.0 / (1.0 + math.log1p(w))


def betweenness(adj: dict, weighted: bool = False) -> dict:
    """Brandes (2001) exact betweenness, normalised to [0,1].
    weighted=True uses Dijkstra with tie_cost(), so brokerage over strong ties counts more."""
    import heapq
    bc = dict.fromkeys(adj, 0.0)
    for s in adj:
        stack, pred = [], {v: [] for v in adj}
        sigma = dict.fromkeys(adj, 0)
        sigma[s] = 1
        if not weighted:
            dist = dict.fromkeys(adj, -1)
            dist[s] = 0
            q = deque([s])
            while q:
                v = q.popleft()
                stack.append(v)
                for w in adj[v]:
                    if dist[w] < 0:
                        dist[w] = dist[v] + 1
                        q.append(w)
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)
        else:
            dist, settled, c = {s: 0.0}, set(), 1
            pq = [(0.0, 0, s)]
            while pq:
                d, _, v = heapq.heappop(pq)
                if v in settled:
                    continue
                settled.add(v)
                stack.append(v)
                for w, wt in adj[v].items():
                    if w in settled:
                        continue
                    nd = d + tie_cost(wt)
                    if w not in dist or nd < dist[w] - 1e-12:
                        dist[w], sigma[w], pred[w] = nd, sigma[v], [v]
                        heapq.heappush(pq, (nd, c, w))
                        c += 1
                    elif abs(nd - dist[w]) <= 1e-12:
                        sigma[w] += sigma[v]
                        pred[w].append(v)
        delta = dict.fromkeys(adj, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    n = len(adj)
    scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 1.0  # undirected: each pair counted twice
    return {v: b * scale for v, b in bc.items()}


def _w(x: float) -> float:
    return math.log1p(x)


def eigenvector(adj: dict, iters: int = 200, tol: float = 1e-9) -> dict:
    """Power iteration on log-damped weights (so one chatty pair cannot dominate)."""
    if not adj:
        return {}
    x = dict.fromkeys(adj, 1.0)
    for _ in range(iters):
        nx_ = {v: x[v] + sum(x[u] * _w(w) for u, w in adj[v].items()) for v in adj}  # +x[v] = shift, avoids oscillation
        norm = math.sqrt(sum(v * v for v in nx_.values())) or 1.0
        nx_ = {v: val / norm for v, val in nx_.items()}
        if sum(abs(nx_[v] - x[v]) for v in adj) < tol:
            x = nx_
            break
        x = nx_
    m = max(x.values()) or 1.0
    return {v: val / m for v, val in x.items()}


def pagerank(adj: dict, personalization: dict | None = None, d: float = 0.85, iters: int = 100) -> dict:
    """(Personalised) PageRank. With personalization = case seeds this is a 'suspicion diffusion' score."""
    n = len(adj)
    if not n:
        return {}
    if personalization:
        tot = sum(personalization.values())
        tele = {v: personalization.get(v, 0) / tot for v in adj}
    else:
        tele = dict.fromkeys(adj, 1 / n)
    out = {v: sum(_w(w) for w in adj[v].values()) for v in adj}
    pr = dict(tele)
    for _ in range(iters):
        dangling = sum(pr[v] for v in adj if out[v] == 0)
        new = {v: (1 - d) * tele[v] + d * dangling * tele[v] for v in adj}
        for v in adj:
            if out[v]:
                share = d * pr[v] / out[v]
                for u, w in adj[v].items():
                    new[u] += share * _w(w)
        pr = new
    return pr


def bfs_dist(adj: dict, s, removed=None) -> dict:
    dist = {s: 0}
    q = deque([s])
    while q:
        v = q.popleft()
        for w in adj[v]:
            if w not in dist and w != removed:
                dist[w] = dist[v] + 1
                q.append(w)
    return dist


def global_efficiency(adj: dict, nodes=None, removed=None, denom_n=None) -> float:
    """Latora-Marchiori efficiency: mean of 1/d(u,v). Robust to disconnection.
    denom_n keeps the original network size so a removed node's own pairs count as lost."""
    nodes = [v for v in (nodes or adj) if v != removed]
    n = denom_n or len(nodes)
    if n < 2:
        return 0.0
    ns = set(nodes)
    sub = {v: {u: w for u, w in adj[v].items() if u in ns} for v in nodes}
    tot = 0.0
    for s in nodes:
        for t, d in bfs_dist(sub, s).items():
            if d:
                tot += 1.0 / d
    return tot / (n * (n - 1))


def components(adj: dict, nodes=None) -> list[set]:
    nodes = set(nodes or adj)
    seen, comps = set(), []
    for s in nodes:
        if s in seen:
            continue
        comp, q = {s}, deque([s])
        while q:
            v = q.popleft()
            for w in adj[v]:
                if w in nodes and w not in comp:
                    comp.add(w)
                    q.append(w)
        seen |= comp
        comps.append(comp)
    return comps


def louvain(adj: dict, seed_order=None) -> dict:
    """Louvain modularity optimisation (Blondel et al. 2008), deterministic node order."""
    # working graph: {node: {nbr: w}} with self loops allowed
    G = {v: dict(nb) for v, nb in adj.items()}
    member = {v: v for v in adj}  # original node -> current super-node
    while True:
        m2 = sum(sum(nb.values()) for nb in G.values())  # = 2m
        if m2 == 0:
            break
        k = {v: sum(nb.values()) for v, nb in G.items()}
        comm = {v: v for v in G}
        tot = dict(k)
        order = sorted(G, key=str)
        improved, moved = True, False
        while improved:
            improved = False
            for v in order:
                cv = comm[v]
                links = {}
                for u, w in G[v].items():
                    if u != v:
                        links[comm[u]] = links.get(comm[u], 0) + w
                tot[cv] -= k[v]
                best, gain = cv, links.get(cv, 0) - tot[cv] * k[v] / m2
                for c, l in links.items():
                    g = l - tot[c] * k[v] / m2
                    if g > gain + 1e-12:
                        best, gain = c, g
                tot[best] += k[v]
                if best != cv:
                    comm[v] = best
                    improved = moved = True
        if not moved:
            break
        # aggregate
        newG: dict = {}
        for v, nb in G.items():
            cv = comm[v]
            newG.setdefault(cv, {})
            for u, w in nb.items():
                cu = comm[u]
                newG[cv][cu] = newG[cv].get(cu, 0) + w
        member = {o: comm[s] for o, s in member.items()}
        if len(newG) == len(G):
            break
        G = newG
    labels, out = {}, {}
    for o, c in sorted(member.items(), key=lambda x: str(x[0])):
        labels.setdefault(c, len(labels))
        out[o] = labels[c]
    return out


def modularity(adj: dict, part: dict) -> float:
    m2 = sum(sum(nb.values()) for nb in adj.values())
    if not m2:
        return 0.0
    k = {v: sum(nb.values()) for v, nb in adj.items()}
    tot: dict = {}
    for v, c in part.items():
        tot[c] = tot.get(c, 0) + k[v]
    inside = sum(w for v, nb in adj.items() for u, w in nb.items() if part[u] == part[v])
    return inside / m2 - sum((t / m2) ** 2 for t in tot.values())


def shortest_path(adj: dict, s, t, banned=frozenset()):
    """Weighted shortest path where strong ties are 'shorter' (cost = 1/(1+log(1+w)))."""
    import heapq
    dist, prev = {s: 0.0}, {}
    pq = [(0.0, str(s), s)]
    while pq:
        d, _, v = heapq.heappop(pq)
        if v == t:
            break
        if d > dist.get(v, math.inf):
            continue
        for u, w in adj[v].items():
            if u in banned and u != t:
                continue
            nd = d + 1.0 / (1.0 + _w(w))
            if nd < dist.get(u, math.inf):
                dist[u], prev[u] = nd, v
                heapq.heappush(pq, (nd, str(u), u))
    if t not in dist:
        return None
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return path[::-1]


def adamic_adar(adj: dict, candidates) -> list[tuple]:
    """Link prediction for non-adjacent pairs: sum over common neighbours of 1/log(deg)."""
    out = []
    cand = list(candidates)
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if b in adj[a]:
                continue
            common = set(adj[a]) & set(adj[b])
            if len(common) < 2:
                continue
            s = sum(1.0 / math.log(len(adj[c])) for c in common if len(adj[c]) > 1)
            out.append((a, b, s, sorted(common)))
    out.sort(key=lambda x: -x[2])
    return out


def simple_cycles(dadj: dict, max_len: int = 5, limit: int = 50) -> list[list]:
    """Bounded DFS for directed cycles (money round-tripping). Each cycle reported once."""
    cycles, seen = [], set()
    nodes = sorted(dadj, key=str)
    idx = {v: i for i, v in enumerate(nodes)}
    for s in nodes:
        stack = [(s, [s])]
        while stack:
            v, path = stack.pop()
            for w in dadj.get(v, ()):
                if w == s and len(path) >= 2:
                    key = frozenset(path)
                    if key not in seen:
                        seen.add(key)
                        cycles.append(list(path))
                        if len(cycles) >= limit:
                            return cycles
                elif w not in path and len(path) < max_len and idx.get(w, -1) > idx[s]:
                    stack.append((w, path + [w]))
    return cycles


def normalise(d: dict) -> dict:
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return dict.fromkeys(d, 0.0)
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}
