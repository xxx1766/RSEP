package revisiontrace
import("encoding/json";"os";"path/filepath";"sync";"testing")
func TestConcurrentRecorder(t *testing.T){
 Enabled=true; events=nil
 var wg sync.WaitGroup
 for i:=0;i<8;i++ {wg.Add(1);go func(){defer wg.Done();for j:=0;j<100;j++{Mark("r","phase", "e")}}()};wg.Wait()
 path:=filepath.Join(t.TempDir(),"trace.json");if err:=Flush(path);err!=nil{t.Fatal(err)}
 b,_:=os.ReadFile(path);var got []Event;if err:=json.Unmarshal(b,&got);err!=nil{t.Fatal(err)}
 if len(got)!=800{t.Fatalf("lost events: %d",len(got))};for i:=1;i<len(got);i++{if got[i].Ns<got[i-1].Ns{t.Fatal("not sorted")}}
 Enabled=false;Mark("disabled","phase", "");if len(events)!=800{t.Fatal("disabled recorder wrote event")}
}
