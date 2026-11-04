(* =====================================================================================
   Section S05_IO_Out   (MAST task, LAST section)
   Writes the command word and speed reference into the ATV930 device DDT (scanned by the
   BMENOC0311). (No lamp outputs: this board's only I/O is safety I/O, which belongs to the
   SAFE task.)
   EDIT each line to the element in the Data Editor, then remove the comment marks.
   ===================================================================================== *)

(* ---- ATV930 ---- *)
(* ATV930.Outputs.CMD  := WORD_TO_INT(wATV_CMD);      EDIT: command word *)
(* ATV930.Outputs.LFRD := iATV_SpeedRef;              EDIT: speed reference, rpm *)
(* ATV930.Outputs.ACC  := iATV_Acc;                   EDIT: acceleration, 0.1 s units (optional) *)
(* ATV930.Outputs.DEC  := iATV_Dec;                   EDIT: deceleration, 0.1 s units (optional) *)
