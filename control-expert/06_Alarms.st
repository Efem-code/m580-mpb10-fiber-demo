(* =====================================================================================
   Section S06_Alarms   (MAST task, after S05_IO_Out)
   Latched alarms with acknowledge, timestamps and a history, for any screen to use:
   the dashboard's Alarms tab reads them, and an HMI or SCADA screen can use the same data.

   Each alarm:
     aAlmCond[n]    the live condition (spare 12 is ours to fill in)
     aAlmText[n]    its name
     aAlmPrio[n]    1 critical, 2 warning
     aAlmState[n]   0 normal   1 active, not acknowledged   2 active, acknowledged
                    3 cleared, not acknowledged (stays until acknowledged)
     aAlmActive[n], aAlmUnack[n]   the same as two BOOLs

   To acknowledge from a screen: xAlmAckAll TRUE (it resets itself), or set a bit in
   aAlmCmd[0] (bit 0 = alarm 1). aAlmCmd[1] = 1 clears the history.

   aAlm AT %MW1300 : ARRAY[0..443] OF INT, for screens that read over Modbus
     [0] events so far    [1] active bits    [2] unacknowledged bits
     [3] active count     [4] unacknowledged count     [5] next history slot (0-63)
     [6] year  [7] MMDD  [8] HHMM  [9] seconds: the PLC clock now, BCD (%SW53..%SW50)
     [10 + 4 * (n - 1)] .. +3   when alarm n was last raised: year, MMDD, HHMM, seconds
     [60 + 6 * slot]            history, 64 events, oldest overwritten:
         +0 event number   +1 100 * type + alarm number (type 1 raised, 2 cleared,
         3 acknowledged)   +2 .. +5 year, MMDD, HHMM, seconds
   ===================================================================================== *)

(* ---- names and priorities, once ---- *)
IF NOT xAlmInit THEN
    xAlmInit := TRUE;
    aAlmText[1]  := 'Drive fault';               aAlmPrio[1]  := 1;
    aAlmText[2]  := 'Drive connection lost';     aAlmPrio[2]  := 1;
    aAlmText[3]  := 'SIG300 connection lost';    aAlmPrio[3]  := 2;
    aAlmText[4]  := 'MPB10 data lost';           aAlmPrio[4]  := 2;
    aAlmText[5]  := 'RIO drop lost';             aAlmPrio[5]  := 1;
    aAlmText[6]  := 'RIO ring open';             aAlmPrio[6]  := 2;
    aAlmText[7]  := 'Vibration warning';         aAlmPrio[7]  := 2;
    aAlmText[8]  := 'Vibration alarm';           aAlmPrio[8]  := 1;
    aAlmText[9]  := 'Temperature warning';       aAlmPrio[9]  := 2;
    aAlmText[10] := 'Dashboard link lost';       aAlmPrio[10] := 2;
    aAlmText[11] := 'Shock detected';            aAlmPrio[11] := 2;
    aAlmText[12] := 'Spare';                     aAlmPrio[12] := 2;
END_IF;

(* ---- the conditions ---- *)
aAlmCond[1]  := xATV_Fault;
aAlmCond[2]  := NOT xATV_Health;
aAlmCond[3]  := NOT xSIG300_Health;
aAlmCond[4]  := xSIG300_Health AND NOT xMPB_DataOK;
aAlmCond[5]  := NOT xDrop_Health;
aAlmCond[6]  := xRingDiagMapped AND xDrop_Health AND NOT xRing_Closed;
aAlmCond[7]  := xVibWarning;
aAlmCond[8]  := xVibAlarm;
aAlmCond[9]  := xTempWarning;
aAlmCond[10] := xDashboardAllowed AND NOT xDashOK;
tofAlmShock(IN := xShock, PT := t#5s);           (* a shock is short: hold it past the on-delay *)
aAlmCond[11] := tofAlmShock.Q;
aAlmCond[12] := FALSE;                           (* spare: our own condition goes here *)

(* ---- commands from a screen ---- *)
wAlmAckMask := INT_TO_WORD(aAlmCmd[0]);
IF xAlmAckAll THEN
    wAlmAckMask := 16#0FFF;
END_IF;
xAlmAckAll := FALSE;
aAlmCmd[0] := 0;

IF aAlmCmd[1] = 1 THEN
    FOR iAlmIdx := 60 TO 443 DO
        aAlm[iAlmIdx] := 0;
    END_FOR;
    iAlmHead := 0;
    iAlmSeq := 0;
END_IF;
aAlmCmd[1] := 0;

(* ---- each alarm: an on-delay, then raise, clear or acknowledge ---- *)
rtAlmTick(CLK := %S5);                           (* 100 ms *)
wAlmActive := 0;
wAlmUnack := 0;
iAlmActCount := 0;
iAlmUnackCount := 0;

FOR iAlm := 1 TO 12 DO
    IF NOT aAlmCond[iAlm] THEN
        aAlmTimer[iAlm] := 0;
    ELSIF rtAlmTick.Q AND (aAlmTimer[iAlm] < iAlmDelay) THEN
        aAlmTimer[iAlm] := aAlmTimer[iAlm] + 1;
    END_IF;
    xAlmNow := aAlmCond[iAlm] AND (aAlmTimer[iAlm] >= iAlmDelay);
    wAlmBit := SHL(INT_TO_WORD(1), iAlm - 1);

    iAlmType := 0;
    IF xAlmNow AND NOT aAlmActive[iAlm] THEN
        aAlmActive[iAlm] := TRUE;
        aAlmUnack[iAlm] := TRUE;
        iAlmType := 1;
        iAlmOfs := 10 + 4 * (iAlm - 1);
        aAlm[iAlmOfs]     := %SW53;
        aAlm[iAlmOfs + 1] := %SW52;
        aAlm[iAlmOfs + 2] := %SW51;
        aAlm[iAlmOfs + 3] := %SW50;
    ELSIF aAlmActive[iAlm] AND NOT xAlmNow THEN
        aAlmActive[iAlm] := FALSE;
        iAlmType := 2;
    ELSIF aAlmUnack[iAlm] AND ((wAlmAckMask AND wAlmBit) <> 0) THEN
        aAlmUnack[iAlm] := FALSE;
        iAlmType := 3;
    END_IF;

    (* one history line per alarm per scan. An acknowledge landing on the same scan as a clear is dropped: press it again *)
    IF iAlmType > 0 THEN
        iAlmSeq := iAlmSeq + 1;
        IF iAlmSeq >= 30000 THEN iAlmSeq := 1; END_IF;
        iAlmOfs := 60 + 6 * iAlmHead;
        aAlm[iAlmOfs]     := iAlmSeq;
        aAlm[iAlmOfs + 1] := 100 * iAlmType + iAlm;
        aAlm[iAlmOfs + 2] := %SW53;
        aAlm[iAlmOfs + 3] := %SW52;
        aAlm[iAlmOfs + 4] := %SW51;
        aAlm[iAlmOfs + 5] := %SW50;
        iAlmHead := (iAlmHead + 1) MOD 64;
    END_IF;

    IF aAlmActive[iAlm] THEN
        wAlmActive := wAlmActive OR wAlmBit;
        iAlmActCount := iAlmActCount + 1;
    END_IF;
    IF aAlmUnack[iAlm] THEN
        wAlmUnack := wAlmUnack OR wAlmBit;
        iAlmUnackCount := iAlmUnackCount + 1;
    END_IF;
    IF aAlmActive[iAlm] AND aAlmUnack[iAlm] THEN
        aAlmState[iAlm] := 1;
    ELSIF aAlmActive[iAlm] THEN
        aAlmState[iAlm] := 2;
    ELSIF aAlmUnack[iAlm] THEN
        aAlmState[iAlm] := 3;
    ELSE
        aAlmState[iAlm] := 0;
    END_IF;
END_FOR;

(* ---- the header of the Modbus block ---- *)
aAlm[0] := iAlmSeq;
aAlm[1] := WORD_TO_INT(wAlmActive);
aAlm[2] := WORD_TO_INT(wAlmUnack);
aAlm[3] := iAlmActCount;
aAlm[4] := iAlmUnackCount;
aAlm[5] := iAlmHead;
aAlm[6] := %SW53;
aAlm[7] := %SW52;
aAlm[8] := %SW51;
aAlm[9] := %SW50;
