# 该脚本用于: 完成EduRAG项目的整体流程, 即: 接收用户问题 -> BM25算阈值 -> 决定执行MySQL FQA 还是 Milvus RAG -> 获取答案 -> 返回给用户
# 你可以把这个版本理解为: 融合了MySQL的RAG系统 V1版本 -> 基础融合.


import warnings
warnings.filterwarnings("ignore")

# todo 1. 导包.
# 导入 MySQL 系统组件，用于数据库操作和搜索
from mysql_qa.cache.redis_client import RedisClient
from mysql_qa.db.mysql_client import MySQLClient
from mysql_qa.retrieval.bm25_search import BM25Search
# 导入 RAG 系统组件，用于知识库检索和答案生成
from rag_qa.core.vector_store import VectorStore
from rag_qa.core.rag_system import RAGSystem
# 导入配置和日志工具，用于系统配置和日志记录
from base.config import config
from base.logger import logger
# 导入 OpenAI 客户端，用于调用 DashScope API
from openai import OpenAI
# 导入时间库，用于记录处理时间
import time

# todo 2. 集成问答系统的核心类.
# 该类的作用: 集成问答系统的核心功能(主类), 把各种工具拼起来干活.
# 先查MySQL中的现成答案 -> 靠谱就用 -> 不靠谱就用RAG重新生成 -> 啥都没有就说 没找到.
class IntegratedQASystem:
    # todo 2.1 初始化方法 -> 创建系统需要的各种工具.
    def __init__(self):
        # 1. 初始化日志工具，用于记录系统运行信息 和 配置对象.
        self.logger = logger
        self.config = config
        # 2. 初始化 MySQL 客户端 和 Redis客户端(缓存), BM25 搜索模块
        self.mysql_client = MySQLClient()
        self.redis_client = RedisClient()
        self.bm25_search = BM25Search(self.redis_client, self.mysql_client)
        # 3. 初始化大语言模型 -> LLM客户端.
        try:
            # 初始化 OpenAI 客户端，连接 DashScope API
            self.client = OpenAI(api_key=self.config.DASHSCOPE_API_KEY, base_url=self.config.DASHSCOPE_BASE_URL)
        except Exception as e:
            # 记录 OpenAI 初始化失败的错误日志
            self.logger.error(f"OpenAI 客户端初始化失败: {e}")
            # 抛出异常，终止初始化
            raise

        # 4. 初始化向量数据库(用于 RAG 系统的知识库管理) 和RAG系统
        self.vector_store = VectorStore()                                   # 创建向量数据库实例 -> 包括: 向量存储 和 检索.
        self.rag_system = RAGSystem(self.vector_store, self.call_dashscope) # 创建 RAG 系统实例 -> 包括: 检索和生成.


    # todo 2.2 调用大语言模型的方法: 给RAG系统用的.
    def call_dashscope(self, prompt):
        """
        函数作用: 调用DashScope的大模型生成文本 -> 例如: 答案, 子问题.
        :param prompt: 输入给模型的提示文本(字符串) -> 即: 用户输入的查询问题.
        :return: 模型生成的文本, 如果调用失败, 就返回错误提示.
        """
        # 1.定义调用 DashScope API 的方法，生成自然语言答案
        try:
            # 2.创建聊天完成请求，调用 DashScope API
            completion = self.client.chat.completions.create(
                model=self.config.LLM_MODEL,  # 使用配置中的语言模型
                messages=[
                    {"role": "system", "content": "你是一个靠谱的助手, 根据给的信息好好回答问题。"},  # 系统提示
                    {"role": "user", "content": prompt},  # 用户输入的提示
                ]
            )
            # 3. 检查响应是否有效，返回答案内容
            return completion.choices[0].message.content if completion.choices else "错误：无效的 LLM 响应"
        except Exception as e:
            # 记录 API 调用失败的错误日志
            self.logger.error(f"LLM 调用失败: {e}")
            # 返回错误信息，便于调试
            return f"错误：LLM 调用失败 - {e}"


    # todo 2.3 处理查询的核心方法 -> 用户问的问题从这里进.
    def query(self, query, source_filter=None):
        """
        函数功能: 处理用户的问题, 按步骤来, 先查MySQL + BM25 -> 不行再RAG -> 最后返回答案.
        :param query: 用户问的问题, 例如: AI课程学什么?
        :param source_filter: 来源过滤条件, 例如: ai -> 就只查ai相关的文档.
        :return: 最终的答案.
        """
        # 1. 记录开始时间, 看看处理完成要多久.
        start_time = time.time()
        self.logger.info(f'开始处理问题: {query}, 来源过滤条件: {source_filter or "不限"}')

        # 2. 调用BM25查MySQL里的答案, 返回2个内容: 答案本身, 以及是否需要RAG的标识.
        answer, need_rag = self.bm25_search.search(query, threshold=0.85)

        # 3. 根据BM25处理结果,判断.
        if answer:
            # 情况1: 拿到靠谱的答案, 直接返回.
            self.logger.info(f"BM25找到了靠谱答案: {answer[:50]}")     # 输出答案可能太长了, 我们只显示前50个字符.
            # 算一下用了多久.
            processing_time = time.time() - start_time
            # 打印结果.
            self.logger.info(f"处理完成, 用时: {processing_time:.3f}秒")
            # 返回处理结果.
            return answer
        elif need_rag:
            # 情况2: 答案不靠谱, 但是有点关联, 调用RAG系统重新生成.
            self.logger.info(f"BM25的答案不靠谱, 让RAG系统来处理...")
            answer = self.rag_system.generate_answer(query, source_filter=source_filter)
            self.logger.info(f"RAG系统生成了答案: {answer[:50]}")
            # 算一下用了多久.
            processing_time = time.time() - start_time
            self.logger.info(f"处理完成, 用时: {processing_time:.3f}秒")
            # 返回处理结果.
            return answer
        else:
            # 情况3: 啥相关的都没找到, 告诉用户没答案.
            self.logger.info(f'BM25没找到任何答案.')
            # 计算用时.
            processing_time = time.time() - start_time
            # 打印结果.
            self.logger.info(f"处理完成, 用时: {processing_time:.3f}秒")
            return '没有找到和你问题相关的答案, 换个问题试试吧!'


# todo 3. 主函数 -> 给用户用的交互式界面.
def main():
    """
    函数功能: 系统的入口, 用户可以在这里输入问题, 选: 来源过滤, 然后看答案.
    :return: 无.
    """
    # 1. 先初始化集成QA系统.
    qa_system = IntegratedQASystem()
    qa_system.logger.info('系统初始化好了, 可以开始问问题了!')

    try:
        # 2. 打印欢迎信息, 告诉用户怎么操作.
        print('\n------------------------------集成问答系统------------------------------')
        # 打印支持的学科类别
        print(f'支持的来源: {qa_system.config.VALID_SOURCES}')       # ["ai", "java", "test", "ops", "bigdata"]
        # 用法
        print("用法: 输入问题按回车键查看答案, 输入exit按回车键退出系统。")

        # 3. 循环等待用户录入.
        while True:
            # 3.1 接收用户录入的问题.
            query = input('\n请录入您的问题: ').strip()
            # 3.2 判断是否是exit, 如果是, 就退出.
            if query.lower() == 'exit':
                qa_system.logger.info('用户输入了exit, 准备退出系统...')
                print('拜拜啦, 祝大家一路顺风, 高薪就业!')
                break
            # 3.3 让用户来选 过滤源.
            source_filter = input(f'请输入来源过滤(可选, 支持: {"./".join(qa_system.config.VALID_SOURCES)}, 直接回车表示不限)').strip()
            # 3.4 检查用户录入的来源过滤对不对.
            if source_filter:
                # 走这里, 说明用户选了过滤源 -> 即: 过滤源非空.
                if source_filter not in qa_system.config.VALID_SOURCES:
                    # 走这里, 说明用户输入的过滤源不在支持的列表中, 提示一下, 然后不用做过滤.
                    qa_system.logger.warning(f'用户输入了无效的来源: {source_filter}')
                    # 细节: 过滤源重置为空.
                    source_filter=None
                else:
                    # 走这里, 说明用户输入的过滤源在支持的列表中, 就提示即可.
                    qa_system.logger.info(f'用户选择了来源过滤: {source_filter}')

            # 3.5 调用查询方法, 拿到答案并提示.
            answer = qa_system.query(query, source_filter=source_filter)
            print(f'\n答案: {answer}')

    except Exception as e:
        # 4. 捕获异常, 记录错误日志, 提示用户.
        qa_system.logger.error(f"系统处理异常: {e} 可能需要重启系统")
        print(f'处理问题时出错: {e}, 请联系管理员, 联系电话: 12345678!')
    finally:
        # 5. 不管有没有错, 都要关掉MySQL连接, 不然会占资源.
        qa_system.mysql_client.close()
        qa_system.logger.info('MySQL连接关闭, 系统退出.')


# todo 4. 程序的主入口.
if __name__ == '__main__':
    main()      # 启用交互式界面, 让用户输入问题, 获取答案.