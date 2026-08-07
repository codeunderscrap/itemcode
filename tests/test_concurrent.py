"""Agent D - Task 3 (concurrency) and part of Task 5 (leases).

Runs against a throwaway SQLite file (never data/itemcode.db - other agents
may be mid-test against the real one), so this is safe to run any time.

    python tests/test_concurrent.py

Proves:
  * ~20 simultaneous group-number allocations never produce a duplicate code
    (Task 3 - BEGIN IMMEDIATE + retry-once-on-busy in C.claim_group_code).
  * a leased block and the main allocator minting at the same time never
    overlap (Task 5's explicit done-when).
  * returning an unused lease creates vacancies that the next arrival fills,
    queue-claim, lowest-first (Task 5).
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db as D          # noqa: E402
from core import codes as C       # noqa: E402

N_THREADS = 20
TEST_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_concurrent_test.db")


def _fresh_db():
    for ext in ("", "-wal", "-shm"):
        p = TEST_DB + ext
        if os.path.exists(p):
            os.remove(p)
    con = D.connect(TEST_DB)
    D.init(con)
    con.execute("INSERT INTO head(name,code2) VALUES('Test Head','ZZ')")
    con.execute("INSERT INTO subhead(head_id,name,code2) VALUES(1,'Test Sub','ZZ')")
    con.commit()
    con.close()


# --------------------------------------------------------------- Task 3
def test_concurrent_group_allocation():
    """~20 creators submitting in the same second must not receive the
    same group number."""
    _fresh_db()
    results, errors = {}, {}

    def worker(i):
        con = D.connect(TEST_DB)
        try:
            r = C.claim_group_code(con, 1, f"Concurrent Group {i}")
            results[i] = r["code3"]
        except Exception as e:                       # pragma: no cover
            errors[i] = repr(e)
        finally:
            con.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"allocation errors: {errors}"
    codes = list(results.values())
    assert len(codes) == N_THREADS, f"expected {N_THREADS} codes, got {len(codes)}"
    assert len(set(codes)) == N_THREADS, f"DUPLICATE codes issued: {codes}"

    con = D.connect(TEST_DB)
    stored = [r["code3"] for r in con.execute("SELECT code3 FROM grp WHERE subhead_id=1")]
    con.close()
    assert len(set(stored)) == N_THREADS, "duplicate rows landed in grp"
    assert sorted(stored) == [f"{n:03d}" for n in range(1, N_THREADS + 1)], \
        "queue-claim should hand out a dense run starting at 001"

    print(f"test_concurrent_group_allocation OK - {N_THREADS} threads, "
          f"{N_THREADS} distinct codes: {sorted(codes)}")


# --------------------------------------------------------------- Task 5
def test_lease_and_main_allocator_never_overlap():
    """Mint from a lease and from the main allocator at the same time -
    the two number spaces must be disjoint by construction, not by timing."""
    _fresh_db()
    con = D.connect(TEST_DB)
    lease = C.grant_lease(con, ("group", 1), size=10)
    con.close()

    lease_results, main_results, errors = {}, {}, {}

    def lease_worker(i):
        con = D.connect(TEST_DB)
        try:
            lease_results[i] = C.mint_from_lease(con, ("group", 1))
        except Exception as e:                        # pragma: no cover
            errors[("lease", i)] = repr(e)
        finally:
            con.close()

    def main_worker(i):
        con = D.connect(TEST_DB)
        try:
            r = C.claim_group_code(con, 1, f"Main Group {i}")
            main_results[i] = r["code3"]
        except Exception as e:                        # pragma: no cover
            errors[("main", i)] = repr(e)
        finally:
            con.close()

    threads = [threading.Thread(target=lease_worker, args=(i,)) for i in range(lease["size"])]
    threads += [threading.Thread(target=main_worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"errors: {errors}"
    lease_values = list(lease_results.values())
    assert len(set(lease_values)) == lease["size"], \
        f"lease itself issued a duplicate under concurrent minting: {lease_values}"
    lease_codes = set(lease_values)
    main_codes = set(main_results.values())
    assert len(main_codes) == N_THREADS, f"main allocator issued a duplicate: {main_results}"
    overlap = lease_codes & main_codes
    assert not overlap, f"lease and main allocator issued the SAME code: {overlap}"
    leased_block = {f"{n:03d}" for n in range(lease["lo"], lease["hi"] + 1)}
    assert lease_codes <= leased_block, "lease minted outside its own block"
    assert main_codes.isdisjoint(leased_block), \
        "main allocator issued a number inside the leased block"

    print(f"test_lease_and_main_allocator_never_overlap OK - lease block "
          f"{lease['range']} ({sorted(lease_codes)}), main allocator picked "
          f"{sorted(main_codes)}, no overlap")


def test_return_lease_creates_vacancy_then_queue_claim_fills_it():
    """On sync: unused leased numbers become vacancies, and the next
    arrival fills them, lowest-first - leasing leaves no permanent holes."""
    _fresh_db()
    con = D.connect(TEST_DB)
    lease = C.grant_lease(con, ("group", 1), size=5)

    # simulate the local server actually using two of its five leased
    # numbers to create real groups while offline
    used = []
    for i in range(2):
        n = C.mint_from_lease(con, ("group", 1))
        used.append(n)
        con.execute("INSERT INTO grp(subhead_id,name,code3,labels) VALUES(1,?,?,'{}')",
                    (f"Offline Group {i}", n))
        con.commit()  # otherwise this bare INSERT leaves an implicit
        # transaction open that collides with the next mint_from_lease's
        # own BEGIN IMMEDIATE

    # the other three were never used - give them back on sync
    returned = C.return_lease(con, ("group", 1))

    assert set(used).isdisjoint(returned)
    assert sorted(used + returned) == [f"{n:03d}" for n in range(lease["lo"], lease["hi"] + 1)]

    code3, freed_from = C.next_group_code(con, 1)
    assert code3 == min(returned), "the next arrival should take the LOWEST returned number"
    con.close()
    print(f"test_return_lease_creates_vacancy_then_queue_claim_fills_it OK - "
          f"used offline: {used}, returned unused: {returned}, next arrival claimed {code3}")


def test_item_position_vacancy_queue_claim():
    """The item-level twin of the group test: a group with no distinguishing
    spec falls back to a running serial (next_item_position), and a freed
    position is offered to the next item that lands there, lowest free
    first - not extended past."""
    _fresh_db()
    con = D.connect(TEST_DB)
    gid = con.execute(
        "INSERT INTO grp(subhead_id,name,code3,labels) VALUES(1,'G','001','{}')").lastrowid

    def place_item(name):
        pos = C.next_item_position(con, gid, [None, None, None, None])
        serial = pos[:2]  # the fallback always lands in the first slot
        # get-or-create, same pattern real callers (resolve.commit,
        # restructure._recode_item_into_group) use for a specval value
        row = con.execute("SELECT id FROM specval WHERE grp_id=? AND slot=1 AND value=?",
                          (gid, f"serial {serial}")).fetchone()
        sid = row["id"] if row else con.execute(
            "INSERT INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
            (gid, 1, f"serial {serial}", serial)).lastrowid
        con.execute("INSERT INTO item(code,name,grp_id,s1) VALUES(?,?,?,?)",
                    (f"ZZZZ001{pos}", name, gid, sid))
        con.commit()
        return pos

    p1 = place_item("A")
    p2 = place_item("B")
    assert p1 == "01000000"
    assert p2 == "02000000", "second item must not collide with the first"

    # B never reached ERPNext, so moving it away frees its position
    b = D.one(con, "SELECT * FROM item WHERE name='B'")
    C.free_item_position(con, gid, p2, [None, None, None, None], "B")
    con.execute("DELETE FROM item WHERE id=?", (b["id"],))
    con.commit()

    p3 = place_item("C")
    assert p3 == p2, "the next arrival must take the lowest FREED position, not extend past it"

    con.close()
    print(f"test_item_position_vacancy_queue_claim OK - A={p1} B={p2} "
          f"(freed) C reclaimed {p3}")


if __name__ == "__main__":
    test_concurrent_group_allocation()
    test_lease_and_main_allocator_never_overlap()
    test_return_lease_creates_vacancy_then_queue_claim_fills_it()
    test_item_position_vacancy_queue_claim()
    for ext in ("", "-wal", "-shm"):
        p = TEST_DB + ext
        if os.path.exists(p):
            os.remove(p)
    print("\nALL TESTS PASSED")
