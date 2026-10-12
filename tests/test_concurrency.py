import asyncio
import sys
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.session import async_session
from database.models import RoomModel, QueueModel, QueueEntry, UserRole
from accessors.rooms import create_room_with_unique_code, delete_room
from accessors.queues import atomic_join_queue, atomic_exit_queue, get_queue_with_data


async def run_concurrency_test():
    print("=== Starting Concurrency & Race Condition Verification ===")

    test_creator_id = 999900000
    room_name = "ConcurrencyTestRoom"

    # 1. Create Room and Queue
    async with async_session() as session:
        room = await create_room_with_unique_code(room_name, test_creator_id, session=session)
        queue = QueueModel(name="TestQueue", room_id=room.id)
        session.add(queue)
        await session.commit()
        queue_id = queue.id
        room_id = room.id
        print(f"Created Test Room ID={room_id}, Queue ID={queue_id}")

    try:
        # 2. Test: 20 simultaneous users joining at the exact same millisecond
        print("\n--- Test 1: 20 Concurrent Joins ---")
        num_users = 20

        async def join_worker(user_idx):
            async with async_session() as session:
                user_id = 999910000 + user_idx
                success, msg, _ = await atomic_join_queue(
                    session=session,
                    queue_id=queue_id,
                    user_id=user_id,
                    username=f"student_{user_idx}",
                    first_name=f"Student",
                    last_name=f"{user_idx}"
                )
                await session.commit()
                return success, msg

        results = await asyncio.gather(*(join_worker(i) for i in range(num_users)))
        all_succeeded = all(r[0] for r in results)
        print(f"All 20 joins succeeded: {all_succeeded}")
        assert all_succeeded, "Not all joins succeeded!"

        # Verify DB state
        async with async_session() as session:
            q = await get_queue_with_data(session, queue_id)
            positions = sorted([e.position for e in q.entries])
            expected_positions = list(range(1, num_users + 1))
            print(f"Actual positions: {positions}")
            assert positions == expected_positions, f"Race condition detected! Positions mismatch: {positions} != {expected_positions}"
            print(">>> PASS: Positions are strictly 1..20 with zero duplicates and zero gaps!")

        # 3. Test: Duplicate Join Prevention
        print("\n--- Test 2: Duplicate Join Prevention ---")
        async with async_session() as session:
            success, msg, _ = await atomic_join_queue(
                session=session,
                queue_id=queue_id,
                user_id=999910000 + 5, # user 5 is already in
                username="student_5"
            )
            await session.commit()
            print(f"Duplicate join result: success={success}, msg='{msg}'")
            assert not success, "Duplicate join was not rejected!"
            print(">>> PASS: Duplicate join correctly rejected!")

        # 4. Test: 5 Concurrent Exits & Atomic Reindexing
        print("\n--- Test 3: 5 Concurrent Exits ---")
        async def exit_worker(user_idx):
            async with async_session() as session:
                user_id = 999910000 + user_idx
                success, msg, _ = await atomic_exit_queue(session, queue_id, user_id)
                await session.commit()
                return success

        exit_indices = [2, 5, 8, 12, 17] # 5 random users exit concurrently
        exit_results = await asyncio.gather(*(exit_worker(i) for i in exit_indices))
        assert all(exit_results), "Some exits failed!"

        # Verify DB state after concurrent exits
        async with async_session() as session:
            q = await get_queue_with_data(session, queue_id)
            positions = sorted([e.position for e in q.entries])
            expected_positions = list(range(1, (num_users - len(exit_indices)) + 1))
            print(f"Positions after 5 exits: {positions}")
            assert positions == expected_positions, f"Reindexing race condition! Positions: {positions} != {expected_positions}"
            print(">>> PASS: Remaining entries reindexed cleanly to 1..15 without gaps!")

        print("\n=== ALL CONCURRENCY TESTS PASSED SUCCESSFULLY! ===")

    finally:
        # Cleanup
        async with async_session() as session:
            await delete_room(room_id, session=session)
            await session.commit()
            print(f"Cleaned up Test Room ID={room_id}")


if __name__ == "__main__":
    asyncio.run(run_concurrency_test())
