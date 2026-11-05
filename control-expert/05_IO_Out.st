(* =====================================================================================
   Section S05_IO_Out   (MAST task, LAST section)
   Writes the command word and speed reference into the ATV930 device DDT (scanned by the
   BMENOC0311), and a vibration bar to the SICK SLT060 on SIG300 port S2.
   Needs the SIG300's Exclusive Owner connection: Input Only has no outputs.
   ACC and DEC need those two channels added to the ATV9xx connection in its DTM first.
   ===================================================================================== *)

(* ---- ATV930 ---- *)
ATV9xx.Outputs.CMD  := wATV_CMD;                 (* command word *)
ATV9xx.Outputs.LFRD := iATV_SpeedRef;            (* speed reference, rpm *)
(* ATV9xx.Outputs.ACC  := iATV_Acc;                 acceleration, 0.1 s units *)
(* ATV9xx.Outputs.DEC  := iATV_Dec;                 deceleration, 0.1 s units *)

(* ---- stack light: SICK SLT060 on SIG300 port S2, as a vibration bar ----
   The SLT060 must be in Level Meter Mode (ISDU 120 = 1, in SOPAS or the SIG300's web page).
   Two bytes of process data: byte 40 of the SIG300 output block holds the level, 0-100 %;
   byte 41 is the buzzer (bit 5), unused here. Set its 20 segments there too: colors (ISDU 262)
   1-8 green, 9-16 yellow, 17-20 red; style (ISDU 263) all continuous. We scale the bar so the
   colors follow the live limits:
     0 to warning = 0-40 % (green)    warning to alarm = 40-80 % (yellow)    alarm to 10 mm/s = 80-100 % (red)
   A drive fault shows full; lost sensor data blinks full. *)
IF xATV_Fault THEN
    rSltLevel := 100.0;
ELSIF xSensorCommAlarm THEN
    IF %S6 THEN rSltLevel := 100.0; ELSE rSltLevel := 0.0; END_IF;
ELSIF rVibVelocity < rVibWarnLimit THEN
    rSltLevel := rVibVelocity / rVibWarnLimit * 40.0;
ELSIF (rVibVelocity < rVibAlarmLimit) AND (rVibAlarmLimit > rVibWarnLimit) THEN
    rSltLevel := 40.0 + (rVibVelocity - rVibWarnLimit) / (rVibAlarmLimit - rVibWarnLimit) * 40.0;
ELSIF rVibAlarmLimit < 10.0 THEN
    rSltLevel := 80.0 + (rVibVelocity - rVibAlarmLimit) / (10.0 - rVibAlarmLimit) * 20.0;
ELSE
    rSltLevel := 100.0;
END_IF;
iSltLevel := LIMIT(0, REAL_TO_INT(rSltLevel), 100);
SIG300_Revision_1_1_from_EDS.Outputs.IOL_Process_DataAI := INT_TO_BYTE(iSltLevel);
SIG300_Revision_1_1_from_EDS.Outputs.IOL_Process_DataAJ := 16#00;
