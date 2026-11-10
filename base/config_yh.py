# 该脚本用于: 项目配置管理.
#
# 工程化要点:
#   1. 三层取值: 环境变量 > config.ini > 代码默认值, 并记录每一项的实际来源, 便于排障.
#   2. 敏感项(数据库密码 / API Key)不再硬编码默认值; 缺失时按 strict 模式报错或告警.
#   3. VALID_SOURCES 使用 json.loads 解析, 不再使用 eval.
#   4. describe() 输出脱敏摘要, 可安全写入启动日志.
#   5. [logger] log_file 现在真正生效(旧版忽略该项).
#
# 说明: 旧版"逐项 config.get + 硬编码 fallback"的实现已移除.
#       需要对照请查看 git 历史: git show HEAD:base/config.py

import ast                      # 安全解析字面量, 用于 valid_sources 的兜底解析
import configparser             # 解析 INI 配置文件
import json                     # 解析 valid_sources (JSON 数组)
import os                       # 路径操作
import warnings                 # 非严格模式下提示配置缺失
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------- 路径常量
# 以"当前文件"为锚点反推项目根目录, 与启动时的工作目录无关.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DEFAULT_CONFIG_FILE = os.path.join(PROJECT_ROOT, 'config.ini')

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
DATA_DIR = os.path.join(PROJECT_ROOT, 'rag_qa', 'data')
MODELS_DIR = os.path.join(PROJECT_ROOT, 'rag_qa', 'models')
EDU_DOCUMENT_LOADERS_DIR = os.path.join(PROJECT_ROOT, 'rag_qa', 'edu_document_loaders')


class ConfigError(RuntimeError):
    """配置缺失或取值非法时抛出, 便于上层统一捕获与提示."""


# 敏感项: 摘要中一律脱敏; 严格模式下缺失即报错.
SENSITIVE_KEYS = frozenset({'MYSQL_PASSWORD', 'REDIS_PASSWORD', 'DASHSCOPE_API_KEY'})

# 用于区分"没有传默认值"与"默认值就是 None"
_UNSET = object()


# ---------------------------------------------------------------- 类型转换器
def _to_int(value: Any) -> int:
    """把配置字符串转成 int, 失败时给出可定位的异常."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f'期望整数, 实际取到 {value!r}') from exc


def _to_list(value: Any) -> List[str]:
    """把 ["ai", "java"] 这类字面量解析成列表; 不使用 eval."""
    if isinstance(value, (list, tuple)):
        return list(value)

    text = str(value).strip()
    try:
        parsed = json.loads(text)                      # 首选: ini 里写的本来就是 JSON 数组
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)            # 兜底: 兼容单引号写法
        except (ValueError, SyntaxError) as exc:
            raise ConfigError(f'valid_sources 不是合法的列表字面量: {text!r}') from exc

    if not isinstance(parsed, (list, tuple)):
        raise ConfigError(f'valid_sources 解析结果应为列表, 实际为 {type(parsed).__name__}')
    return [str(item) for item in parsed]


# ---------------------------------------------------------------- 配置类
class Config:
    def __init__(self, config_file: Optional[str] = None, strict: Optional[bool] = None):
        # 1. 目录属性: 供其它模块拼接路径使用
        self.PROJECT_ROOT = PROJECT_ROOT
        self.LOG_DIR = LOG_DIR
        self.DATA_DIR = DATA_DIR
        self.MODELS_DIR = MODELS_DIR
        self.EDU_DOCUMENT_LOADERS_DIR = EDU_DOCUMENT_LOADERS_DIR

        # 2. 配置文件定位: 传参优先, 否则用项目根下的 config.ini
        self.config_file = os.path.abspath(config_file) if config_file else DEFAULT_CONFIG_FILE

        # 3. 严格模式: 构造参数优先, 其次环境变量 CONFIG_STRICT=1/true/yes/on
        if strict is None:
            strict = str(os.getenv('CONFIG_STRICT', '')).lower() in {'1', 'true', 'yes', 'on'}
        self.strict: bool = strict

        # 4. 排障用的元信息: 每项来源 / 缺失项 / 告警集合
        self._sources: Dict[str, str] = {}
        self._missing: List[str] = []
        self._warnings: List[str] = []

        # 5. 读取配置文件; 不存在时不崩, 但必须明确告知(不再静默降级)
        #    ExtendedInterpolation 支持 ${section:key} 跨段引用, 且不会因值中带 % 报错.
        self._parser = configparser.ConfigParser(
            interpolation=configparser.ExtendedInterpolation()
        )
        loaded = self._parser.read(self.config_file, encoding='utf-8')
        self.config_loaded: bool = bool(loaded)
        if not self.config_loaded:
            self._warnings.append(f'配置文件不存在或不可读: {self.config_file}')

        # 兼容: 旧代码可能通过 config.config 拿到解析器对象
        self.config = self._parser

        self._load_all()
        self._finalize()

    # ------------------------------------------------------------ 取值核心
    def _resolve(self, name: str, section: str, key: str, *,
                 env: Optional[str] = None,
                 default: Any = _UNSET,
                 cast: Callable[[Any], Any] = str,
                 required: bool = False) -> Any:
        """
        统一取值入口, 顺序: 环境变量 > 配置文件 > 代码默认值.

        :param name:    最终属性名, 同时作为环境变量的默认名称
        :param env:     显式指定环境变量名(如 DASHSCOPE_API_KEY)
        :param default: 代码默认值; 不传表示"没有默认值"
        :param cast:    类型转换函数
        :param required: 为 True 且三层都取不到时, 记入缺失项
        """
        env_name = env or name

        # 第一层: 环境变量(存在即生效, 空字符串也算显式设置)
        if env_name in os.environ:
            raw, source = os.environ[env_name], 'env'
        else:
            # 第二层: 配置文件
            raw = self._parser.get(section, key, fallback=None)
            source = 'ini' if raw is not None else None

        # 第三层: 代码默认值
        if raw is None:
            if default is not _UNSET:
                raw, source = default, 'default'
            elif required:
                self._missing.append(f'{name} (环境变量 {env_name} 或 [{section}] {key})')
                self._sources[name] = 'missing'
                return None
            else:
                self._sources[name] = 'missing'
                return None

        self._sources[name] = source
        if raw is None or cast is str:
            return raw
        return cast(raw)

    def _load_all(self) -> None:
        # ---------------- MySQL ----------------
        self.MYSQL_HOST = self._resolve('MYSQL_HOST', 'mysql', 'host', default='localhost')
        self.MYSQL_USER = self._resolve('MYSQL_USER', 'mysql', 'user', default='root')
        # 密码不给默认值: 曾经的 fallback='123456' 会把"忘记配置"变成静默事故
        self.MYSQL_PASSWORD = self._resolve('MYSQL_PASSWORD', 'mysql', 'password', required=True)
        self.MYSQL_DATABASE = self._resolve('MYSQL_DATABASE', 'mysql', 'database', default='subjects_kg')

        # ---------------- Redis ----------------
        self.REDIS_HOST = self._resolve('REDIS_HOST', 'redis', 'host', default='localhost')
        self.REDIS_PORT = self._resolve('REDIS_PORT', 'redis', 'port', default=6379, cast=_to_int)
        # 本地 Redis 常无密码, 因此 required 但允许空字符串
        self.REDIS_PASSWORD = self._resolve('REDIS_PASSWORD', 'redis', 'password', default='')
        self.REDIS_DB = self._resolve('REDIS_DB', 'redis', 'db', default=0, cast=_to_int)

        # ---------------- Milvus ----------------
        self.MILVUS_HOST = self._resolve('MILVUS_HOST', 'milvus', 'host', default='localhost')
        # 保持字符串类型: 下游用于拼接 URI, 改动会破坏调用方
        self.MILVUS_PORT = self._resolve('MILVUS_PORT', 'milvus', 'port', default='19530')
        self.MILVUS_DATABASE_NAME = self._resolve(
            'MILVUS_DATABASE_NAME', 'milvus', 'database_name', default='itcast')
        self.MILVUS_COLLECTION_NAME = self._resolve(
            'MILVUS_COLLECTION_NAME', 'milvus', 'collection_name', default='edurag_final')

        # ---------------- LLM ----------------
        self.LLM_MODEL = self._resolve('LLM_MODEL', 'llm', 'model', default='qwen-plus')
        self.DASHSCOPE_API_KEY = self._resolve(
            'DASHSCOPE_API_KEY', 'llm', 'dashscope_api_key', required=True)
        self.DASHSCOPE_BASE_URL = self._resolve(
            'DASHSCOPE_BASE_URL', 'llm', 'dashscope_base_url',
            default='https://dashscope.aliyuncs.com/compatible-mode/v1')

        # ---------------- 检索参数 ----------------
        self.PARENT_CHUNK_SIZE = self._resolve(
            'PARENT_CHUNK_SIZE', 'retrieval', 'parent_chunk_size', default=1200, cast=_to_int)
        self.CHILD_CHUNK_SIZE = self._resolve(
            'CHILD_CHUNK_SIZE', 'retrieval', 'child_chunk_size', default=300, cast=_to_int)
        self.CHUNK_OVERLAP = self._resolve(
            'CHUNK_OVERLAP', 'retrieval', 'chunk_overlap', default=50, cast=_to_int)
        self.RETRIEVAL_K = self._resolve(
            'RETRIEVAL_K', 'retrieval', 'retrieval_k', default=5, cast=_to_int)
        self.CANDIDATE_M = self._resolve(
            'CANDIDATE_M', 'retrieval', 'candidate_m', default=2, cast=_to_int)

        # ---------------- 应用 ----------------
        self.VALID_SOURCES = self._resolve(
            'VALID_SOURCES', 'app', 'valid_sources',
            default='["ai", "java", "test", "ops", "bigdata"]', cast=_to_list)
        self.CUSTOMER_SERVICE_PHONE = self._resolve(
            'CUSTOMER_SERVICE_PHONE', 'app', 'customer_service_phone', default='13112345678')

        # ---------------- 日志 ----------------
        # 旧版忽略 [logger] log_file, 这里让配置项真正生效; 相对路径以项目根为基准
        log_file = self._resolve('LOG_FILE', 'logger', 'log_file', default='logs/app.log')
        self.LOG_FILE = log_file if os.path.isabs(log_file) else os.path.join(PROJECT_ROOT, log_file)

    # ------------------------------------------------------------ 校验与诊断
    def _finalize(self) -> None:
        """汇总所有缺失项后统一处理: 严格模式直接失败, 否则仅告警."""
        for item in self._warnings:
            warnings.warn(item, RuntimeWarning, stacklevel=3)

        if not self._missing:
            return

        message = '配置缺失: ' + '; '.join(self._missing)
        if self.strict:
            raise ConfigError(message + '  请在 config.ini 或环境变量中补齐后重试.')
        warnings.warn(
            message + '  (当前为非严格模式, 相应属性为 None, 相关功能会在使用时失败)',
            RuntimeWarning, stacklevel=3)

    def origin(self, name: str) -> str:
        """返回某个配置项的实际来源: env / ini / default / missing."""
        return self._sources.get(name, 'unknown')

    def describe(self, mask: bool = True) -> Dict[str, str]:
        """返回"配置项 -> 值[来源]"的摘要; mask=True 时敏感项只保留末 4 位."""
        summary: Dict[str, str] = {}
        for name in sorted(self._sources):
            value = getattr(self, name, None)
            if mask and name in SENSITIVE_KEYS and isinstance(value, str) and value:
                value = '***' + (value[-4:] if len(value) > 4 else '')
            summary[name] = f'{value}  [{self._sources[name]}]'
        return summary

    def reload(self) -> None:
        """重新读取配置文件(测试或热修复时使用)."""
        self.__init__(self.config_file, self.strict)

    def __repr__(self) -> str:
        return f'Config(file={self.config_file}, strict={self.strict}, ' \
               f'loaded={self.config_loaded}, missing={self._missing})'


# 全局单例: 其它模块 from base.config import config 即可共用同一份配置
config = Config()


if __name__ == '__main__':
    # 自测: 打印脱敏后的配置全景, 确认每一项的来源是否符合预期
    conf = Config()
    print(f'配置文件: {conf.config_file}')
    print(f'是否加载: {conf.config_loaded}')
    print(f'严格模式: {conf.strict}')
    print('-' * 60)
    for key, line in conf.describe().items():
        print(f'{key:28s} = {line}')