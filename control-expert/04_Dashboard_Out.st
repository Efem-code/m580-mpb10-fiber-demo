(* =====================================================================================
   Section S04_Dashboard_Out   (MAST task)
   Fills the status block the dashboard reads.

   aDash AT %MW1000 : ARRAY[0..31] OF INT     (map: Setup tab of the dashboard)
   ===================================================================================== *)

IF iHeartbeat >= 30000 THEN iHeartbeat := 0; END_IF;   (* stay clear of INT overflow (%S18) *)
iHeartbeat := iHeartbeat + 1;
aDash[0] := iHeartbeat;

(* ---- sensor ---- *)
wDashStatus := 0;
wDashStatus.0 := xSIG300_Health;
wDashStatus.1 := xMPB_DataOK;
wDashStatus.2 := xVibWarning;
wDashStatus.3 := xVibAlarm;
wDashStatus.4 := xShock;
wDashStatus.5 := xTempWarning;
aDash[1] := WORD_TO_INT(wDashStatus);

aDash[2] := REAL_TO_INT(rVibVelocity * 100.0);   (* mm/s x100 *)
IF xMPB_DataOK THEN
    aDash[3] := REAL_TO_INT(rVibX * 100.0);      (* mm/s x100 *)
    aDash[4] := REAL_TO_INT(rVibY * 100.0);
    aDash[5] := REAL_TO_INT(rVibZ * 100.0);
ELSE
    aDash[3] := -32768;                          (* -32768 = not available *)
    aDash[4] := -32768;
    aDash[5] := -32768;
END_IF;
aDash[6] := REAL_TO_INT(rTemperature * 10.0);    (* degC x10 *)
aDash[7] := iShockCount;
aDash[8] := WORD_TO_INT(wMPB_Alarm);

FOR iDashIdx := 0 TO 9 DO                        (* raw 20 bytes, high byte first *)
    aDash[9 + iDashIdx] := WORD_TO_INT(SHL(BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 2 * iDashIdx]), 8)
                                       OR BYTE_TO_WORD(aSIG300_In[iPD_Ofs + 2 * iDashIdx + 1]));
END_FOR;
aDash[19] := 0;

(* ---- drive ---- *)
wDashDrive := 0;
wDashDrive.0 := xATV_Health;
wDashDrive.1 := xATV_Ready;
wDashDrive.2 := xATV_Running;
wDashDrive.3 := xATV_Fault;
wDashDrive.4 := xDashboardAllowed;
wDashDrive.5 := xDashOK;
wDashDrive.6 := xReverse;
aDash[20] := WORD_TO_INT(wDashDrive);
aDash[21] := WORD_TO_INT(wATV_ETA);
aDash[22] := iATV_Speed;
aDash[23] := iATV_SpeedRef;
aDash[24] := iATV_Current;
aDash[25] := iATV_Torque;
aDash[26] := iATV_Thermal;
aDash[27] := iATV_LastFault;
aDash[28] := WORD_TO_INT(wATV_CMD);

(* ---- stack light state, shown on the dashboard ---- *)
xLampGreen := xATV_Running AND NOT xATV_Fault;
xLampAmber := xMPB_DataOK AND (xVibWarning OR xTempWarning);
xLampRed   := xATV_Fault OR (xMPB_DataOK AND xVibAlarm) OR (xSensorCommAlarm AND %S6);   (* %S6: 1 s blink *)

(* ---- RIO drop and enable (bits 1-3 and 10 were the operator panel; always 0 on this board) ---- *)
wDashPanel := 0;
wDashPanel.0  := xDrop_Health;
wDashPanel.4  := xDemoEnable;
wDashPanel.5  := xLampGreen;
wDashPanel.6  := xLampAmber;
wDashPanel.7  := xLampRed;
wDashPanel.8  := xRing_Closed;
wDashPanel.9  := xRingDiagMapped;
aDash[29] := WORD_TO_INT(wDashPanel);

(* ---- ramps in use ---- *)
aDash[30] := iATV_Acc;                          (* s x10 *)
aDash[31] := iATV_Dec;
