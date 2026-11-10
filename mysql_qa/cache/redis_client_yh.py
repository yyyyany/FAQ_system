# 该脚本用于: Redis缓存技术.

# 导包
import redis
import json
import os, sys

# todo 0.路径相关 -> 如下的设置是为了 <<将项目根目录加入系统路径>>, 绝大多数电脑不设置也可以, 如果你直接导包遇到问题了, 请执行如下设置.
# # 1.获取当前文件所在目录的绝对路径.
# current_dir = os.path.dirname(os.path.abspath(__file__))
# print(f'current_dir: {current_dir}')
#
# # 2. 获取当前目录的上级目录.
# module_dir = os.path.dirname(current_dir)
# print(f'module_dir: {module_dir}')
#
# # 3. 获取项目根目录的绝对路径.
# project_root = os.path.dirname(module_dir)
# print(f'project_root: {project_root}')
#
# # 4. 将项目根目录加入系统路径, 确保跨目录导入自定义模块(例如: config, logger)...
# sys.path.insert(0, project_root)

# 5. 导入自定义配置 和 日志模块.
from base import Config, logger


# todo 0.5 答案缓存的统一约定 -> 前缀 和 默认过期时间(秒).
#  前缀: 让 "答案缓存" 和 其他业务缓存(如分词结果) 在扁平命名空间里分开.
#  TTL : 默认 1 小时后自动删除, 避免问答库更新后旧答案一直残留.
ANSWER_KEY_PREFIX = 'answer:'
ANSWER_TTL = 3600


# todo 1.定义Redis客户端类 -> 封装Redis连接, 数据存储, 数据获取, 答案查询等功能.
class RedisClient:
    # todo 1.1 初始化方法, 获取Redis连接, 初始化日志, 处理连接异常.
    def __init__(self):
        # 1. 获取日志记录器.
        self.logger = logger

        # 2. 建立Redis连接.
        try:
            self.client = redis.StrictRedis(
                host=Config().REDIS_HOST,           # Redis数据库主机地址
                port=Config().REDIS_PORT,           # Redis数据库端口号
                password=Config().REDIS_PASSWORD,   # Redis数据库密码
                db=Config().REDIS_DB,               # Redis数据库编号
                decode_responses=True               # 自动将返回的字节数据转换为字符串(避免手动解码)
            )
        except redis.RedisError as e:
            self.logger.error(f'Redis连接异常: {e}')
            raise   # 声明抛出异常.

        # 3. 记录INFO日志
        self.logger.info('Redis连接成功')

    # todo 1.2 存储数据到Redis -> 将Python对象序列化为json字符串存储.
    def set_data(self, key, value):
        try:
            # 1. 存储数据.
            self.client.set(key, json.dumps(value, ensure_ascii=False)) # ensure_ascii: 保证中文等非ASCII字符正常显式.
            # 2. 记录INFO日志.
            self.logger.info(f'Redis存储数据成功: {key}')
        except redis.RedisError as e:
            self.logger.error(f'Redis存储数据失败: {e}')


    # todo 1.3 从Redis中获取数据 -> 反序列化为Python对象.
    def get_data(self, key):
        try:
            # 1. 获取数据.
            data = self.client.get(key)
            # 2. 处理结果 -> 反序列化为Python对象, 再返回.
            return json.loads(data) if data else None
        except redis.RedisError as e:
            self.logger.error(f'Redis获取数据失败: {e}')
            return None     # 保持方法的返回值一致性.

    # todo 1.4 构建答案缓存的键 -> 读 和 写 都必须调用它, 保证拼法永远一致.
    @staticmethod
    def _answer_key(query):
        # 1. strip(): 避免 '熵' 和 '熵 ' 被当成两个不同的键, 白白多存一份.
        # 2. 统一加前缀: 格式只在这里定义一次, 以后要改(如换成 md5)只需改这一行.
        return f'{ANSWER_KEY_PREFIX}{query.strip()}'

    # todo 1.5 写入答案缓存 -> 将答案序列化后存入, 并设置过期时间.
    def set_answer(self, query, answer, ttl=ANSWER_TTL):
        key = self._answer_key(query)
        try:
            # 1. 存储数据 -> ex 指定过期秒数, 到期自动删除.
            self.client.set(key, json.dumps(answer, ensure_ascii=False), ex=ttl)
            # 2. 记录INFO日志.
            self.logger.info(f'Redis缓存答案成功: {key}')
        except redis.RedisError as e:
            # 3. 缓存失败不影响主流程 -> 只记日志, 不抛异常.
            self.logger.error(f'Redis缓存答案失败: {e}')

    # todo 1.6 读取答案缓存 -> 反序列化后返回, 和 set_answer 严格对称.
    def get_answer(self, query):
        key = self._answer_key(query)
        try:
            # 1. 取原始字符串 -> decode_responses=True, 拿到的已是 str.
            raw = self.client.get(key)
            # 2. 用 is None 判断 -> 空字符串答案('') 也是合法缓存, 不能用 if not raw.
            if raw is None:
                return None
            # 3. 反序列化 -> 与 set_answer 里的 json.dumps 配对, 去掉那层引号.
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # 4. 兜底: 兼容早期用 set_data 写入的脏数据, 原样返回, 不炸主流程.
                self.logger.warning(f'缓存值不是合法JSON, 按原文返回: {key}')
                return raw
        except redis.RedisError as e:
            self.logger.error(f'从Redis中获取答案失败: {e}')
            return None


# todo 2.测试代码
if __name__ == '__main__':
    # 1. 实例化RedisClient客户端.
    redCli = RedisClient()

    # 2. 获取Redis中所有的键.
    print(redCli.client.keys("*"))

    # 3. 写入 -> 再读出, 验证 "存进去的" 和 "取出来的" 完全一致(不带多余引号).
    redCli.set_answer('熵的解释', '熵是描述系统混乱程度的物理量。')
    print('读一次:', repr(redCli.get_answer('熵的解释')))
    print('带空格:', repr(redCli.get_answer('  熵的解释  ')))   # 与上面同一个键
    print('不存在:', repr(redCli.get_answer('不存在的胡子')))
