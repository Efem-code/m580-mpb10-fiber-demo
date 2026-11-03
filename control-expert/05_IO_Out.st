(* =====================================================================================
   Section S05_IO_Out   (MAST task, LAST section)
   Writes the command word and speed reference into the ATV930 device DDT (scanned by the
   BMENOC0301) and the stack light to the X80 outputs on the BMECRA31210 RIO drop.
   EDIT each line to the element in the Data Editor, then remove the comment marks.
   ===================================================================================== *)

(* ---- ATV930 ---- *)
(* ATV930.Outputs.CMD  := WORD_TO_INT(wATV_CMD);      EDIT: command word *)
(* ATV930.Outputs.LFRD := iATV_SpeedRef;              EDIT: speed reference, rpm *)
(* ATV930.Outputs.ACC  := iATV_Acc;                   EDIT: acceleration, 0.1 s units (optional) *)
(* ATV930.Outputs.DEC  := iATV_Dec;                   EDIT: deceleration, 0.1 s units (optional) *)

(* ---- stack light on the RIO drop ---- *)
(* <DO module>.DIS_CH_OUT[0].VALUE := xLampGreen;     EDIT: green, motor running *)
(* <DO module>.DIS_CH_OUT[1].VALUE := xLampAmber;     EDIT: amber, vibration / temperature warning *)
(* <DO module>.DIS_CH_OUT[2].VALUE := xLampRed;       EDIT: red, alarm or drive fault; blinks = no sensor data *)
