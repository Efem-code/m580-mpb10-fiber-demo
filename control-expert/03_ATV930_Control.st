(* =====================================================================================
   Section S03_ATV930_Control   (MAST task)
   Runs the ATV930 through the CiA402 (Drivecom) state machine over EtherNet/IP
   (the drive is scanned by the BMENOC0301).

   Who can start the motor (the last Start wins, iRunSource remembers who):
     1  operator panel : Start push-button on the RIO drop, runs at iPanelSpeed
     2  dashboard      : only when xDashboardAllowed AND its watchdog is OK
     3  engineer       : xLocalRun / iLocalSpeed from an animation table (commissioning)
   Any stop wins: panel Stop (N/C), dashboard Stop, xLocalStop, enable key off, drive fault,
   lost drive connection. A run that was started by the panel also stops if the RIO drop is
   lost; one started by the dashboard stops when the dashboard drops its run request or its
   watchdog expires. Starting again always needs a fresh Start (rising edge).
   The E-stop is NOT in this program: it is hard-wired to the ATV930 STO inputs.

   ETA (status word), masked:
     xxxx xxxx x1xx 0000  (ETA AND 16#004F) = 16#0040  switch on disabled
     xxxx xxxx x01x 0001  (ETA AND 16#006F) = 16#0021  ready to switch on
     xxxx xxxx x01x 0011  (ETA AND 16#006F) = 16#0023  switched on
     xxxx xxxx x01x 0111  (ETA AND 16#006F) = 16#0027  operation enabled (running)
     bit 3 = fault
   CMD (command word):
     16#0006 shutdown -> ready   16#0007 switch on (stops on ramp)   16#000F run
     + 16#0800 reverse           16#0080 fault reset (rising edge)
   ===================================================================================== *)

(* ---- where the drive is ---- *)
wATV_State   := wATV_ETA AND 16#006F;
xATV_Fault   := xATV_Health AND wATV_ETA.3;
xATV_Running := xATV_Health AND (wATV_State = 16#0027);
xATV_Ready   := xATV_Health AND NOT xATV_Fault
                AND ((wATV_State = 16#0021) OR (wATV_State = 16#0023) OR xATV_Running);

(* ---- who asks for what ---- *)
xPanelStop := xDrop_Health AND NOT xPB_StopNC;
xStopAny   := xPanelStop OR xDashStop OR xLocalStop OR NOT xDemoEnable;

rtPanelStart(CLK := xDrop_Health AND xPB_Start);
rtDashRun(CLK := xDashRun);
rtLocalRun(CLK := xLocalRun);

IF xStopAny OR xATV_Fault OR NOT xATV_Health
   OR ((iRunSource = 1) AND NOT xDrop_Health)
   OR ((iRunSource = 2) AND NOT xDashRun)
   OR ((iRunSource = 3) AND NOT xLocalRun) THEN
    iRunSource := 0;
ELSIF rtLocalRun.Q THEN
    iRunSource := 3;
ELSIF rtDashRun.Q THEN
    iRunSource := 2;
ELSIF rtPanelStart.Q THEN
    iRunSource := 1;
END_IF;

CASE iRunSource OF
    1: iSpeedReq := iPanelSpeed;  xReverse := FALSE;
    2: iSpeedReq := iDashSpeed;   xReverse := xDashReverse;
       IF xDashJog THEN iSpeedReq := MIN(iSpeedReq, iJogSpeedMax); END_IF;   (* hold-to-run jog *)
    3: iSpeedReq := iLocalSpeed;  xReverse := xLocalReverse;
ELSE
    iSpeedReq := 0;               xReverse := FALSE;
END_CASE;
iSpeedReq := LIMIT(0, iSpeedReq, iMaxSpeed);    (* never above the bench limit *)

(* ---- ramps: the dashboard's while it is in control, otherwise the PLC defaults (s x10) ---- *)
IF xDashOK AND (iDashAcc > 0) THEN
    iATV_Acc := LIMIT(1, iDashAcc, 600);
ELSE
    iATV_Acc := iAccDefault;
END_IF;
IF xDashOK AND (iDashDec > 0) THEN
    iATV_Dec := LIMIT(1, iDashDec, 600);
ELSE
    iATV_Dec := iDecDefault;
END_IF;

xRunReq   := iRunSource <> 0;
xRunCmd   := xRunReq AND xDemoEnable AND xATV_Health;
xResetReq := xLocalReset OR xDashReset OR (xDrop_Health AND xPB_Reset);

(* ---- the command word ---- *)
IF NOT xATV_Health THEN
    wATV_CMD := 16#0000;                         (* no connection: the drive's own comm-fault response applies *)
ELSIF xATV_Fault THEN
    IF xResetReq THEN
        wATV_CMD := 16#0080;                     (* fault reset: rising edge of bit 7 *)
    ELSE
        wATV_CMD := 16#0000;
    END_IF;
ELSIF (wATV_ETA AND 16#004F) = 16#0040 THEN
    wATV_CMD := 16#0006;                         (* switch on disabled -> ready to switch on *)
ELSIF NOT xRunCmd THEN
    wATV_CMD := 16#0007;                         (* switched on, motor stops on its ramp *)
ELSIF wATV_State = 16#0021 THEN
    wATV_CMD := 16#0007;                         (* ready -> switched on *)
ELSE
    wATV_CMD := 16#000F;                         (* switched on / running -> operation enabled *)
    IF xReverse THEN
        wATV_CMD := wATV_CMD OR 16#0800;
    END_IF;
END_IF;

IF xRunCmd THEN
    iATV_SpeedRef := iSpeedReq;
ELSE
    iATV_SpeedRef := 0;
END_IF;
