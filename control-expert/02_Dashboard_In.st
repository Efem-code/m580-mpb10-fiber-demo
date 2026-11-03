(* =====================================================================================
   Section S02_Dashboard_In   (MAST task)
   Reads the dashboard's command block and runs the watchdog.

   aCmd AT %MW1100 : ARRAY[0..4] OF INT
     [0] dashboard heartbeat (changes every ~200 ms while the dashboard is in control)
     [1] bits: 0 run, 1 stop, 2 fault reset, 3 reverse, 4 jog
     [2] speed request, rpm
     [3] accel ramp, s x10 (0 = use iAccDefault)
     [4] decel ramp, s x10 (0 = use iDecDefault)

   xDashboardAllowed : PLC-side permission (animation table or key switch). FALSE = the
                       dashboard can watch but never start the motor.
   ===================================================================================== *)

(* watchdog: heartbeat must change at least once a second *)
xDashHbChanged := aCmd[0] <> iDashHbLast;
iDashHbLast    := aCmd[0];
tonDashWd(IN := NOT xDashHbChanged, PT := t#1s);
xDashOK := xDashboardAllowed AND NOT tonDashWd.Q;

wDashCmd := INT_TO_WORD(aCmd[1]);

xDashRun     := xDashOK AND wDashCmd.0 AND NOT wDashCmd.1;
xDashStop    := wDashCmd.1 AND NOT tonDashWd.Q;  (* honoured even without xDashboardAllowed, but a stale
                                                    stop bit (dashboard gone) can't block the panel *)
xDashReset   := xDashOK AND wDashCmd.2;
xDashReverse := xDashOK AND wDashCmd.3;
xDashJog     := xDashOK AND wDashCmd.4;
iDashSpeed   := aCmd[2];
iDashAcc     := aCmd[3];
iDashDec     := aCmd[4];
