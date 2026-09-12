我的建议是：不要在“先实验”和“先修复”之间二选一，而是采用“最小确定性修复 → 被动实验 → 根据证据继续修复”的顺序。
原因很直接：当前代码里已经存在几个确定错误。如果不先修，实验结果会被崩溃、陈旧快照和错误 player 候选污染；但如果一开始就进行大规模重构，又可能建立在错误假设上。
最佳策略是：
保存当前基线
    ↓
只修复已经确定的问题
    ↓
关闭危险功能，进入只读实验
    ↓
验证一个不确定点
    ↓
根据结果做一小步修改
    ↓
再次验证
    ↓
观测稳定后才接模型
    ↓
影子推理稳定后才允许自动下牌
一、哪些问题应该先修，不需要再做实验
这些已经从代码或现有采样中得到充分证据，应当直接处理。
确定问题	为什么应先修	修复方向
找到的 live_world 没有传给观测编码器	明确的代码路径错误	编码函数显式接收 root/world
manager+0xa8=0x1 可能被当成 world	明确可能导致非法解引用	拒绝小地址、未映射地址和不满足结构的 root
live snapshot 重新绑定时没有完整清空	会让下一局收到上一局数据	snapshot 与 generation 同步重置
player latch 没有绑定 generation	旧地址复用后可能误认为新局 player	将 player roots 放进 generation 结构
控制线程仍可能深读联机内存	会继续产生生命周期竞争	联机 observe 只读取已发布快照
深层 telemetry 默认介入联机对局	当前主要崩溃来源之一	加联机 feature flag，默认关闭
当前运行 probe 与工作区源码难以精确对应	崩溃分析可能对应错版本	状态接口输出完整构建身份和功能开关


这些属于“安全前置修复”，而不是需要验证的设计假设。
二、哪些地方不能先重构，必须通过实验决定
下面的问题存在多种可能答案，应该先采集数据。
不确定点	不应提前做的事情	应通过什么实验决定
联机完整 world 是否存在	不要先把离线 world 逻辑全面移植	Root 稳定性实验
pre-step 还是 post-step 更一致	不要凭感觉固定时序	同时记录前后 tick 和对象数量
player 从哪个字段最可靠	不要继续无限扩大字段扫描范围	人工出牌与 consume Hook 对照
本机账号对应 native owner 0 还是 1	不要硬编码 owner	UI 手牌、圣水与出牌事件对照
哪些 object 类型可进入模型	不要把 vector 中所有对象全部编码	对象分类与生命周期采样
HP 槽位是否适用于全部实体	不要把读不到 HP 当崩溃或统一填 0	分对象类别验证
ADB tap 延迟是否足够	不要直接开发复杂原生注入	端到端触控延迟实验
浅层观测对模型影响多大	不要先重训模型	影子推理与离线降级评估
终局信号是哪一种	不要只依赖 manager 停止	多信号完整对局记录


所以第一轮不应该做：
- 大规模重写 BattleEnvV1；
- 全面改造所有 160 多处深层读取；
- 立即实现原生联机命令注入；
- 立即修改模型结构；
- 立即重新训练模型；
- 直接开启完整自动对战。
三、推荐执行阶段
阶段 0：冻结当前可回退基线
当前工作区存在大量未提交修改，所以动手前必须先建立可追溯基线。
需要记录：
内容	目的
当前 git diff 补丁	保留已有工作
当前源码树状态	确认哪些文件是用户已有修改
已安装 APK SHA-256	确认客户端版本
设备 probe SHA-256	确认实际运行代码
本地编译 probe SHA-256	比较源码构建物与设备文件
libg.so SHA-256/build ID	锁定偏移适用版本
当前 feature flags	确认哪些 telemetry 已开启
当前稳定版 probe 备份	随时恢复


这一步不改变游戏行为。
阶段 1：做最小 P0 修复
只处理已经明确的问题：
1. 定义统一的 LiveRootSet。
2. build_observation_json 不再自行硬读 manager+0xa8。
3. world/root 必须通过严格检查。
4. manager 变化时创建新 generation。
5. generation 变化时清除：
   - live snapshot；
   - player roots；
   - object manager；
   - live event ring；
   - pending action；
   - terminal 状态。
6. 联机模式默认关闭：
   - projectile route；
   - damage source 深层解析；
   - character runtime；
   - remaining runtime；
   - phase 深层运行时；
   - 其他多跳引用。
7. observe 控制线程只复制已经发布的 JSON/POD 快照。
8. 暂时禁止联机动作执行，只允许 observe。
此时修复目标不是让模型对战，而是让实验环境可信。
阶段 2：进行 Root 稳定性实验
先不生成完整观测，只记录根信息：
{
  "generation": 3,
  "snapshotSequence": 184,
  "manager": "...",
  "objectManager": "...",
  "players": ["...", "..."],
  "preTick": 812,
  "postTick": 813,
  "objectCountBefore": 17,
  "objectCountAfter": 18,
  "threadId": 1234
}
建议先跑 10 局人工对战。
验收标准：
指标	要求
客户端崩溃	0
同一局 generation 变化	0，除非明确重挂载
新局沿用旧 generation	0
tick 无故回退	0
旧快照跨局返回	0
player 地址异常跳变	0，或能被正确重新确认
快照线程	始终来自预期游戏线程


这一阶段会决定是否还需要继续寻找完整 world。
阶段 3：浅层观测实验
Root 稳定后，才逐项加入：
1. tick；
2. 双方塔；
3. 己方 player；
4. 己方手牌和圣水；
5. 场上单位身份、owner、位置；
6. HP；
7. 公开出牌事件。
每新增一种数据，都先跑若干局，不要一次全部开启。
建议顺序：
批次	新增内容	验证重点
A	tick + object count	生命周期
B	六座塔身份和位置	owner、坐标
C	塔 HP	component backlink
D	己方 player、手牌、圣水	player 绑定
E	普通单位 ID、owner、位置	对象过滤
F	普通单位 HP	组件有效性
G	consume-card 浅层事件	公开出牌识别


累计至少达到 30～50 局零崩溃后，才认为浅层观测稳定。
阶段 4：建立 LiveBattleEnvV1
原生数据稳定后，在 Python 侧新增联机环境：
live-observation.v1
        ↓
LiveBattleEnvV1
        ↓
FAIR ObservationV1
这时仍不自动下牌。
需要确保：
- 只给模型己方私有信息；
- 对手手牌、完整卡组、精确圣水不进入 FAIR 观测；
- 未知 runtime 字段使用 None/unknown provenance；
- native owner 正确转换为模型 actor owner；
- 坐标通过 PerspectiveTransformV1 统一处理；
- entity ID 在一局内稳定；
- 对局变化后重置 LSTM 状态。
阶段 5：模型影子推理
影子模式是整个流程里非常重要的一步：
读取真实联机状态
→ 构建模型输入
→ 模型生成 ActionV1
→ 记录动作
→ 不向游戏发送
影子模式可以验证：
项目	验证内容
手牌槽位	模型选择的槽位当前是否存在
圣水	模型是否经常选择不可支付的牌
action mask	模型落点是否合法
视角	是否把自己和对手方向反了
延迟	推理完成时快照是否已过期
LSTM	一局内连续、新局正确重置
缺失特征	是否大量 WAIT 或行为异常
稳定性	模型运行是否影响采集频率


至少完成 5～10 局影子推理，才能开启动作。
阶段 6：动作实验
动作也应分级：
级别	行为
A	只标定四个手牌槽位
B	只标定几个固定合法战场格
C	每局最多允许模型执行一张牌
D	动作后等待 ACK，不连续动作
E	每局允许少量动作
F	完整自动对战


单动作阶段建议设置硬限制：
每个 generation 最多自动执行 1 次
这样即使 owner、坐标或 ACK 有问题，也不会把整局变成不可控的连续误操作。
四、一个具体的“修复—实验”循环
推荐每次只围绕一个假设：
假设：
post-step 对象图比 pre-step 一致

修改：
同时采集 pre/post 的浅层计数，不增加深层读取

实验：
人工跑 3～5 局

结果：
post-step 的 player、塔和 object count 更稳定

决策：
正式快照改为 post-step

回归：
再跑 10 局确认没有新增崩溃
再处理下一个假设：
假设：
consume-card 的 owner_root 是最可靠的 player 锚点

实验：
人工分别使用双方/本机可控方式打出若干牌
记录 owner_root、player 圣水、手牌变化

结果：
如果三种信号一致，则正式绑定
如果不一致，则保留候选并继续寻找更强锚点
这种方式比“把所有问题一次修完再测试”更容易定位问题。
五、建议的 Go/No-Go 门槛
阶段	允许进入下一阶段的条件
P0 修复完成	编译和原有离线测试通过
Root 实验完成	10 局零崩溃、零跨局陈旧状态
浅层观测完成	30～50 局零崩溃、关键字段与画面吻合
环境适配完成	FAIR 合约通过、无对手隐藏信息
影子推理完成	5～10 局无异常、动作基本合法
单动作完成	至少 50 次单动作，无重复执行，确认率 ≥98%
完整自动对战	单局生命周期、终局和 LSTM 重置正确
连续运行	至少 50 局无客户端崩溃和跨局污染


如果某个阶段未达到门槛，就不继续扩大功能。
六、哪些代码暂时不要动
为了降低变量数量，第一轮建议暂时不改：
- V4 模型网络结构；
- checkpoint 格式；
- 训练代码；
- 离线 BattleEnvV1 主流程；
- 离线 rich telemetry 行为；
- ObservationV1 和 ActionV1 的公共版本；
- 离线评测器；
- 完整原生命令注入流程。
第一轮修改应集中在：
区域	目的
cr_replay_probe.cpp 联机 attach/snapshot	生命周期和 root 安全
联机 telemetry gate	默认关闭深层读取
live protocol/status	可观测性和版本身份
新增联机实验脚本	自动采样、检查 tick/generation/root
后续新增 LiveBattleEnvV1	对接现有模型契约
后续新增 LiveActionExecutor	ADB 动作与 ACK


七、最终建议
应该先修复，但只修复已经确认的 P0 问题；修完立即进入只读实验。不要先完成一整套重构，也不要带着当前已知错误直接做大量实验。
准确顺序是：
顺序	工作
1	保存和标识当前基线
2	修复 world 传递、小地址检查、generation 清理
3	默认关闭联机深层 telemetry
4	建立只读 root 快照和诊断指标
5	实验验证 root、step 边界、player/owner
6	根据实验结果实现稳定浅层观测
7	新增 LiveBattleEnvV1
8	运行模型影子推理
9	标定 ADB 触控并进行单动作实验
10	完成动作确认后再开放完整自动对战


一句话概括：
先把“测量仪器”修到可信，再用实验决定架构细节；观测没有连续稳定之前，不让模型控制游戏。

这样既能接着现有代码推进，也能避免把未经验证的假设固化成一次大重构。

收到，我现在重新读取实时状态；刚才截图捕获时仍显示大厅，可能正好早于你进入对局。我会连续短轮询，避免错过战斗 world 的挂载窗口。




  运行了命令


已查看 1 张图像



已运行 @'
from native_runner.cr_native_env import NativeClashEnv
import json, time
c=NativeClashEnv(host='127.0.0.1',port=26789,timeout=5.0)
for i in range(12):
 d=c.live_root_diagnostic(); print(json.dumps(d,ensure_ascii=False),flush=True)
 if d.get('worldSeen') or d.get('snapshotPresent'):
  print('PLAYERS',json.dumps(c.live_players(),ensure_ascii=False),flush=True)
  try: print('OBS',json.dumps(c.observe(),ensure_ascii=False)[:20000],flush=True)
  except Exception as e: print('OBSERR',repr(e),flush=True)
  break
 time.sleep(.5)
'@ | python -




这次抓到了关键证据：画面确实在对局内，但 probe 仍绑定的是大厅 manager；与此同时 combat hook 已捕获到对局的 objectManager=0x7727d40b3cf0。说明问题不是“未进入对局”，而是当前 manager→world 的查找路径不适用于这个 live controller。现在应把 combat hook 捕获到的战斗根节点安全地绑定到同一 generation，先修复根节点归属与诊断，再复测。




  运行了命令


我会做一个小范围、可回退的实验性修复：给 combat hook 捕获到的 objectManager/player 加上 live generation 标签，只允许当前正在 step 的 live manager 发布；诊断接口也会拒绝跨 generation 的旧指针。这个改动不会改变游戏逻辑、不会发动作，只是让下一局能可靠验证“直接从战斗对象管理器恢复玩家状态”这条路径。




编辑了文件读取文件运行了命令


修复已通过 AArch64 编译。现在我把新 probe 部署到当前已连接的 emulator-5554，保留旧 .so 备份并重启游戏；重启后需要你再次进入一局，我会马上验证 generation 标签和 live-players。




  运行了命令





新 probe 已部署并通过 SHA 校验，旧版本已备份；控制端也已重新 arm，当前为干净状态：
项目	当前值
armed	true
generation	0
world/objectManager	尚未捕获
新增 generation 标签	已生效


请你现在在模拟器里点击“对战”并进入实际战斗画面。看到双方塔和底部四张卡后，回复我“已进入战斗”，我会立即轮询 live-root-diagnostic 和 live-players。

  已编辑 2 个文件+72-27查看更改撤销审核

native_runner/probe/combat_event_telemetry.incnative_runner/probe/combat_event_telemetry.inc+48-21
native_runner/probe/cr_replay_probe.cppnative_runner/probe/cr_replay_probe.cpp+24-6














我已进入







星期五16:09






用时 1m 58s




当前状态已经准备好，live-observe 保持为 armed=true。
这次没捕获到，是因为当前对局在重新 arm 之前已经创建，而 battle manager 只在进入对局的场景切换瞬间暴露。请完成或退出当前对局后直接再开一局，无需重启游戏。进入下一局、看到双方塔和底部四张卡后，回复我：
第二局已进入

我会立即采集 generation、objectManager、玩家根节点、圣水、卡组和对象向量，全程只读，不会自动下牌。


星期五16:11








第二局已进入  







星期五16:12






收到，立即采集这一局的 generation 绑定、战斗对象管理器和玩家状态；只读，不发送动作。




已运行 @'
from native_runner.cr_native_env import NativeClashEnv
import json,time
c=NativeClashEnv(host='127.0.0.1',port=26789,timeout=5)
for i in range(30):
    d=c.live_root_diagnostic()
    print('D',i,json.dumps(d,ensure_ascii=False),flush=True)
    gen=d.get('generation',0)
    om=d.get('objectManager','0x0')
    omg=d.get('objectManagerGeneration',0)
    if gen and om!='0x0' and omg==gen:
        try:
            p=c.live_players(); print('P',json.dumps(p,ensure_ascii=False),flush=True)
        except Exception as e: print('PE',repr(e),flush=True)
        try:
            o=c.observe(); print('O',json.dumps(o,ensure_ascii=False)[:30000],flush=True)
        except Exception as e: print('OE',repr(e),flush=True)
        break
    time.sleep(.2)
'@ | python -




关键验证已经通过：objectManager 与 generation=1 完全一致，且对象向量有效，目前有 6 个对象（双方各 3 座塔）。world 路径确实不可用，普通 observe() 因而按预期拒绝读取。玩家根节点尚未从最初 6 座塔中恢复，我继续在有单位生成时采样，验证玩家引用、圣水和卡组。




  运行了命令


上一局的日志已经证明新修复有效：在对局期间捕获到了同一 generation 的战斗 objectManager，并且 combat hook 捕获到了两个真实玩家指针（例如 0x772805d4d5c0、0x772805ce03d0）。但我开始轮询时比赛已接近结束，控制接口随后看到的是下一代空 manager。
我现在启动一个 3 分钟高频只读采集器，等你进入下一局后会自动抓取玩家、圣水、卡组和对象数量，不再依赖你精确报时。




  运行了命令


你抓取到了吗？还是说我要再开一局？你要知道一局只有几分钟，且你不允许我下牌，可能2分钟就结束了。














已运行 adb -s emulator-5554 logcat -d -v time | rg "CRNativeProbe\( *4617\).*combat spawn capture|live attach" | Select-Object -Last 40




已经抓到“可用的战斗根”了，但还没有抓到完整 ObservationV1：
- 成功：同一 generation 下的 objectManager、对象数量变化、combat hook 玩家引用。
- 未完成：玩家 0/1 的归属判定。目前扫描器把一个“结构类似但不是玩家根”的对象误认成两个玩家，所以我不会把这份数据喂给模型。
我不建议你现在再盲打一局。我先修正玩家归属：在 spawn hook 中按生成对象的 owner（0/1）分别保存玩家引用，并增加 identity/deck/vector 结构校验，排除刚才发现的 decoy。修完后只需再开一局进行最终只读验证。




编辑了文件运行了命令


玩家归属修复已完成并编译通过。它会：
1. 按生成对象的 owner 分别保存两侧玩家根；
2. 校验 player+0x10 == objectManager；
3. 校验 identity、deck、hand/cycle vector；
4. 拒绝刚才那种 identity=0xf / deck=0x44b5 的 decoy。
我现在部署这个版本并重启游戏。之后只需要再开一局；这次采集器会自动运行，不需要你卡时间。




已运行 $ErrorActionPreference='Stop'; $adb='D:/AndroidSDK/platform-tools/adb.exe'; $serial='emulator-5554'; $pkg='nullsroyale.rel.free'; $activity='com.supercell.clashroyale.GameApp'; $probe='E:/FirstLight_CR/native_runner/probe/out/libcrprobe.so'; $baseLine=(& $adb -s $serial shell pm path $pkg | Select-String 'base.apk' | Select-Object -First 1).ToString().Trim(); $base=$baseLine.Substring(8); $root=$base.Substring(0,$base.Length-('/base.apk').Length); $target="$root/lib/arm64/libcrprobe.so"; $localSha=(Get-FileHash -Algorithm SHA256 -LiteralPath $probe).Hash.ToLowerInvariant(); $remoteStage="/data/local/tmp/crprobe-deploy-$localSha.so"; $oldOut=& $adb -s $serial shell sha256sum $target; $oldSha=($oldOut -split '\s+')[0].ToLowerInvariant(); $backup="/data/local/tmp/libcrprobe-before-$oldSha.so"; & $adb -s $serial shell test -f $backup; if($LASTEXITCODE -ne 0){ & $adb -s $serial shell cp $target $backup }; & $adb -s $serial push $probe $remoteStage | Out-Null; & $adb -s $serial shell am force-stop $pkg; Start-Sleep -Milliseconds 700; & $adb -s $serial shell dd "if=$remoteStage" "of=$target" conv=fsync | Out-Null; & $adb -s $serial shell chmod 0755 $target; & $adb -s $serial shell restorecon $target; $installed=(& $adb -s $serial shell sha256sum $target); if(($installed -split '\s+')[0].ToLowerInvariant() -ne $localSha){throw 'installed hash mismatch'}; & $adb -s $serial shell am start --user 0 -n "$pkg/$activity" | Out-Null; & $adb -s $serial forward tcp:26789 tcp:26789; Write-Output ("installed=$localSha backup=$backup");





Selected model is at capacity. Please try a different model.







  已编辑 2 个文件+57-10查看更改撤销审核

native_runner/probe/combat_event_telemetry.incnative_runner/probe/combat_event_telemetry.inc+9-0
native_runner/probe/cr_replay_probe.cppnative_runner/probe/cr_replay_probe.cpp+48-10








新版本已部署，自动采集器已启动并保持监听。当前状态是：
- generation=1：已捕获场景 manager
- objectManager 尚未挂载：正在等待真正对局场景
- 采集窗口：约 4 分钟
- 动作注入：关闭
现在请进入一局战斗即可；看到双方塔和四张卡后无需马上回复，我会自动采集。




已运行 adb -s emulator-5554 exec-out screencap -p > runs/live_screen_match4_current.png




已查看 1 张图像



已运行 @'
from native_runner.cr_native_env import NativeClashEnv
import json,time,pathlib
c=None
for _ in range(40):
 try:
  c=NativeClashEnv(host='127.0.0.1',port=26789,timeout=3)
  d=c.live_root_diagnostic()
  if not d.get('armed'): c.set_live_observation(True)
  break
 except Exception: time.sleep(.5)
if c is None: raise SystemExit('control unavailable')
out=pathlib.Path('runs/live_direct_capture_match4_20260911.jsonl'); f=out.open('w',encoding='utf-8'); start=time.time(); last=None; found=False
try:
 while time.time()-start<240:
  try:
   d=c.live_root_diagnostic(); gen=d.get('generation',0); om=d.get('objectManager','0x0'); omg=d.get('objectManagerGeneration',0); p=None
   if gen and om!='0x0' and omg==gen: p=c.live_players()
   row={'time':time.time(),'diagnostic':d,'players':p}; f.write(json.dumps(row,ensure_ascii=False)+'\n'); f.flush()
   key=(gen,om,omg,(p or {}).get('count'),tuple(x.get('valid') for x in (p or {}).get('players',[])))
   if key!=last: print(json.dumps({'generation':gen,'objectManager':om,'objectManagerGeneration':omg,'count':(p or {}).get('count'),'players':(p or {}).get('players')},ensure_ascii=False),flush=True); last=key
   if p and all(x.get('valid') for x in p.get('players',[])):
    print('FOUND_BOTH',json.dumps(p,ensure_ascii=False),flush=True)
    try: print('OBS',json.dumps(c.observe(),ensure_ascii=False)[:20000],flush=True)
    except Exception as e: print('OBSERR',repr(e),flush=True)
    found=True; break
  except Exception as e: print('ERR',repr(e),flush=True)
  time.sleep(.1)
finally: f.close()
print('DONE found=%s file=%s'%(found,out.resolve()),flush=True)
'@ | python -




自动采集器仍在运行；当前截图显示还在大厅，所以暂时没有新对局。请现在点击中央“对战”，进入匹配并等待出现双方塔和四张手牌；采集器会自动捕获，无需再次重启。





exceeded retry limit, last status: 429 Too Many Requests











昨天 17:04
上一轮采集窗口已结束（期间你还在大厅）。我重新开启一轮 3 分钟采集，等你现在点击“对战”。这次我会在检测到有效的两侧玩家根后立即输出并保存，不等待整局结束。




进去了














  运行了命令


你检测到了吗？我都开了两轮了？或者你是否可以自己设置一个进行对战的开始脚本呢？














  运行了命令


两轮确实都检测到了战斗对象管理器，但没有得到可用于模型的完整输入：
结果	状态
live generation	已成功
战斗 objectManager	已成功，向量数量动态变化
world 指针	当前版本仍无法从 manager 直接取得
两侧玩家根	hook 能看到候选，但当前校验仍未确认 owner/卡组完整性
ObservationV1	尚未通过，未接入模型


你说得对，手动报时不适合几分钟一局。我可以设置一个“开始对战 + 自动采集”的脚本：它只点击大厅中央“对战”，然后 arm probe、轮询并保存诊断，不会点击卡牌，也不会发送 deploy/inject/play。我现在把这个脚本加入项目并先做静态检查。




编辑了文件运行了命令


脚本已加入并通过 Python 编译检查：[start_live_capture.py](E:/FirstLight_CR/native_runner/start_live_capture.py)。
它会自动完成：
1. 清理并 arm 只读 probe；
2. 点击大厅中央“对战”；
3. 每 100ms 采集 generation、objectManager、玩家候选；
4. 保存到 runs/live_capture_latest.jsonl；
5. 一旦两侧玩家根都通过结构校验就立即停止。
我现在直接运行它，替你自动开始下一局和采集。




  运行了命令


已查看 1 张图像



  运行了命令


检测到了，而且两轮都捕获到了真实战斗对象：
- 第一轮：generation=1，对象向量数量从 6 增长到 16；
- 第二轮：generation=2，对象向量数量动态变化；
- 自动脚本这一轮：成功自动点击“对战”，并捕获 generation=4、有效 objectManager，截图也确认已经进入对局。
但目前仍不能说“模型输入已经成功”：玩家根节点校验没有通过。我进一步核对了内存，发现之前把 spawn object +0x100 当成玩家根节点的假设不成立，那个字段在部分对象上是普通数值或其他引用。因此当前数据不能安全喂给模型，我不会绕过校验强行生成 ObservationV1。




已运行 git status --short; git diff --check -- native_runner/start_live_capture.py native_runner/probe/cr_replay_probe.cpp native_runner/probe/combat_event_telemetry.inc