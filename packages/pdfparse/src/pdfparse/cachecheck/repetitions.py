"""Two independent exact Unicode LRS algorithms; overlapping occurrences count."""
from array import array


def longest_repeat(text):
    """Suffix automaton + end-position counts; expected O(C) time and O(C) space."""
    if len(text) < 2:
        return 0
    transitions = [{}]
    lengths, links, occurrences = [0], [-1], [0]
    last = 0
    for char in text:
        current = len(lengths)
        lengths.append(lengths[last] + 1)
        links.append(0)
        occurrences.append(1)
        transitions.append({})
        p = last
        while p >= 0 and char not in transitions[p]:
            transitions[p][char] = current
            p = links[p]
        if p >= 0:
            q = transitions[p][char]
            if lengths[p] + 1 == lengths[q]:
                links[current] = q
            else:
                clone = len(lengths)
                lengths.append(lengths[p] + 1)
                links.append(links[q])
                occurrences.append(0)
                transitions.append(transitions[q].copy())
                while p >= 0 and transitions[p].get(char) == q:
                    transitions[p][char] = clone
                    p = links[p]
                links[q] = links[current] = clone
        last = current
    # Counting sort avoids O(C log C) sorting of states for suffix-link propagation.
    buckets = array('I', [0]) * (len(text) + 1)
    for length in lengths:
        buckets[length] += 1
    for i in range(1, len(buckets)):
        buckets[i] += buckets[i - 1]
    order = array('I', [0]) * len(lengths)
    for state, length in enumerate(lengths):
        buckets[length] -= 1
        order[buckets[length]] = state
    best = 0
    for state in reversed(order):
        if occurrences[state] >= 2:
            best = max(best, lengths[state])
        if links[state] >= 0:
            occurrences[links[state]] += occurrences[state]
    return best


def longest_repeat_reference(text):
    """Prefix-doubling suffix array O(C log² C), followed by linear Kasai LCP."""
    n = len(text)
    if n < 2:
        return 0
    ranks = [ord(c) for c in text]
    suffixes = list(range(n))
    width = 1
    while width < n:
        def key(i):
            return ranks[i], ranks[i + width] if i + width < n else -1
        suffixes.sort(key=key)
        next_ranks = [0] * n
        for i in range(1, n):
            next_ranks[suffixes[i]] = next_ranks[suffixes[i - 1]] + (key(suffixes[i]) != key(suffixes[i - 1]))
        ranks = next_ranks
        if ranks[suffixes[-1]] == n - 1:
            break
        width *= 2
    inverse = [0] * n
    for rank, suffix in enumerate(suffixes):
        inverse[suffix] = rank
    best = common = 0
    for i in range(n):
        rank = inverse[i]
        if rank == 0:
            common = 0
            continue
        j = suffixes[rank - 1]
        while i + common < n and j + common < n and text[i + common] == text[j + common]:
            common += 1
        best = max(best, common)
        common = max(0, common - 1)
    return best
