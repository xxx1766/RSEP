// Package revisiontrace provides opt-in, buffered instrumentation for Issue 8.
// It never performs file I/O on the request path and changes no runtime policy.
package revisiontrace
import("encoding/json";"os";"sort";"sync";"time")
var Enabled=os.Getenv("TIDE_STAGE_TRACE")!=""
var origin=time.Now()
var mu sync.Mutex
var events []Event
type Event struct {ID string `json:"id"`; Phase string `json:"phase"`; Ns int64 `json:"ns"`; Related string `json:"related"`}
func Mark(id,phase,related string){
 if !Enabled{return};e:=Event{id,phase,time.Since(origin).Nanoseconds(),related}
 mu.Lock();events=append(events,e);mu.Unlock()
}
func Flush(path string)error{
 if !Enabled{return nil};mu.Lock();out:=append([]Event(nil),events...);mu.Unlock()
 sort.SliceStable(out,func(i,j int)bool{return out[i].Ns<out[j].Ns})
 b,err:=json.MarshalIndent(out,"","  ");if err!=nil{return err};return os.WriteFile(path,append(b,'\n'),0600)
}
