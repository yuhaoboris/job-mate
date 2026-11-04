// 纯离线回显：不联网、不创建子进程、不解释参数。
process.stdout.write(JSON.stringify(process.argv.slice(2)));
