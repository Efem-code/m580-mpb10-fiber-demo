(* =====================================================================================
   Section S02_Dashboard_In   (MAST task)
   Reads the dashboard's command block and runs the watchdog.

   aCmd AT %MW1100 : ARRAY[0..4] OF INT
     [0] dashboard heartbeat (changes every ~200 ms while the dashboard is in control)
     [1] bits: 0 run, 1 stop, 2 fault reset, 3 reverse, 4 jog
     [2] speed request, rpm
     [3] accel ramp, s x10 (0 = use iAccDefault)
     [4] decel ramp, s x10 (0 = use iDecDefault)

   xDashboardAllowed : PLC-side permission. FALSE = the dashboard can watch but never start
                       the motor.

   aEnableCmd AT %MW1286 : ARRAY[0..1] OF INT, so a screen can set the two enables without
   Control Expert: [0] xDemoEnable, [1] xDashboardAllowed. 1 = on, 2 = off, cleared here.
   ===================================================================================== *)

(* enables from a screen; they start FALSE again after a download or cold start *)
IF aEnableCmd[0] = 1 THEN xDemoEnable := TRUE;  ELSIF aEnableCmd[0] = 2 THEN xDemoEnable := FALSE;  END_IF;
IF aEnableCmd[1] = 1 THEN xDashboardAllowed := TRUE;  ELSIF aEnableCmd[1] = 2 THEN xDashboardAllowed := FALSE;  END_IF;
aEnableCmd[0] := 0;
aEnableCmd[1] := 0;

(* watchdog: heartbeat must change at least once a second *)
xDashHbChanged := aCmd[0] <> iDashHbLast;
iDashHbLast    := aCmd[0];
tonDashWd(IN := NOT xDashHbChanged, PT := t#1s);
xDashOK := xDashboardAllowed AND NOT tonDashWd.Q;

wDashCmd := INT_TO_WORD(aCmd[1]);

xDashRun     := xDashOK AND wDashCmd.0 AND NOT wDashCmd.1;
xDashStop    := wDashCmd.1 AND NOT tonDashWd.Q;  (* honoured even without xDashboardAllowed, but a stale
                                                    stop bit (dashboard gone) can't block a start from the
                                                    animation table *)
xDashReset   := xDashOK AND wDashCmd.2;
xDashReverse := xDashOK AND wDashCmd.3;
xDashJog     := xDashOK AND wDashCmd.4;
iDashSpeed   := aCmd[2];
iDashAcc     := aCmd[3];
iDashDec     := aCmd[4];
