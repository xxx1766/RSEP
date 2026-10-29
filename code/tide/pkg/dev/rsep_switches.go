package dev

var RSEPTest bool = false
var EnableConcurrencyControl bool = true // 时间隔离：是否开启并发控制，即是否使用队列对Routine进行有序化
var EnableCpuset bool = true             // 空间隔离：是否开启物理资源隔离，即是否开启对容器的绑核
var EnableContainerPerRoutine = true     // 空间隔离：是否开启执行环境隔离，即是否对每个请求分配一个容器
