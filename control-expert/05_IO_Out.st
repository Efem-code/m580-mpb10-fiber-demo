(* =====================================================================================
   Section S05_IO_Out   (MAST task, LAST section)
   Writes the command word and speed reference into the ATV930 device DDT (scanned by the
   BMENOC0311). (No lamp outputs: this board's only I/O is safety I/O, which belongs to the
   SAFE task.)
   ACC and DEC need those two channels added to the ATV9xx connection in its DTM first.
   ===================================================================================== *)

(* ---- ATV930 ---- *)
ATV9xx.Outputs.CMD  := wATV_CMD;                 (* command word *)
ATV9xx.Outputs.LFRD := iATV_SpeedRef;            (* speed reference, rpm *)
(* ATV9xx.Outputs.ACC  := iATV_Acc;                 acceleration, 0.1 s units *)
(* ATV9xx.Outputs.DEC  := iATV_Dec;                 deceleration, 0.1 s units *)
