"""Shared smart-lock handlers.

Tuya BLE locks expose several different controls that are easy to confuse:

- **bolt state** -- the motor/bolt report (``lock_motor_state``), on while the
  bolt is retracted;
- **passage mode** -- the lock is held open until it is turned off again
  (``free_passage_mode``; some locks name the same DP ``automatic_lock`` in the
  cloud);
- **automatic locking** -- the lock relocks itself after each opening; turning
  it off is free passage (``automatic_lock``, the inverse polarity);
- **momentary opening** -- a one-shot command that releases the lock for a few
  seconds before it relocks by itself (``bluetooth_unlock``, ``ble_unlock_check``).

``state`` holds the lock ``read`` handlers, ``command`` the lock ``write`` /
``open`` handlers and the button ``write`` handlers. The data points they use
besides the entity's own ``dp_id`` are named on the descriptor through the
``*_dp_id`` fields each handler declares.
"""
