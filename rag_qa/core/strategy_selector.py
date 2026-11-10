# 该脚本用于: 检索策略选择器.

# 导入 LangChain 提示模板
from langchain_core.prompts import PromptTemplate
# 导入日志和配置
from base.config import config
from base.logger import logger
# 导入 OpenAI
from openai import OpenAI


# todo 1. 定义StrategySelector类: 用于根据用户查询选择最合适的检索增强策略.
class StrategySelector:
    # todo 1.1 初始化方法 -> 创建大模型客户端, 加载策略选择的提示模板.
    def __init__(self):
        # 1. 初始化 OpenAI 客户端
        self.client = OpenAI(api_key=config.DASHSCOPE_API_KEY,
                             base_url=config.DASHSCOPE_BASE_URL)
        # 2. 获取策略选择提示模板
        self.strategy_prompt_template = self._get_strategy_prompt()

    # todo 1.2 调用大模型的API -> 向DashScope发送请求, 获取模型返回结果.
    def call_dashscope(self, prompt):
        """
        调用DashScope大模型API, 获取模型对输入提示的响应.
        :param prompt: 发给大模型的提示文本(字符和窜), 包含: 用户查询和任务要求.
        :return: 大模型返回的文本结果(字符串), 这里是选择的策略名称, 如果调用失败, 直接返回: 直接检索
        """
        try:
            # 1. 创建聊天完成请求 -> 调用大模型, 传入模型名称, 对话内容, 温度参数.
            completion = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个有用的助手，能够根据用户输入的Prompt严格执行并返回可靠的结果"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1
            )
            # 2. 返回完成结果 -> 如果有结果, 返回第1个回答的内容, 否则返回默认策略.
            return completion.choices[0].message.content if completion.choices else "直接检索"
        except Exception as e:
            # 3. 异常处理.
            # 记录 API 调用失败
            logger.error(f"DashScope API 调用失败: {e}")
            # 默认返回直接检索
            return "直接检索"

    # todo 1.3 获取策略选择提示模板 -> 定义引导大模型选择策略的固定格式文本.
    def _get_strategy_prompt(self):
        #   定义私有方法，获取策略选择 Prompt 模板
        return PromptTemplate(
            template="""
            你是一个智能助手，负责分析用户查询 {query}，并从以下四种检索增强策略中选择一个最适合的策略，直接返回策略名称，不需要解释过程。

            以下是几种检索增强策略及其适用场景：

            1.  **直接检索：**
                * 描述：对用户查询直接进行检索，不进行任何增强处理。
                * 适用场景：适用于查询意图明确，需要从知识库中检索**特定信息**的问题，例如：
                    * 示例：
                        * 查询：AI 学科学费是多少？
                        * 策略：直接检索
                    * 查询：JAVA的课程大纲是什么？
                        * 策略：直接检索
            2.  **假设问题检索（HyDE）：**
                * 描述：使用 LLM 生成一个假设的答案，然后基于假设答案进行检索。
                * 适用场景：适用于查询较为抽象，直接检索效果不佳的问题，例如：
                    * 示例：
                        * 查询：人工智能在教育领域的应用有哪些？
                        * 策略：假设问题检索
            3.  **子查询检索：**
                * 描述：将复杂的用户查询拆分为多个简单的子查询，分别检索并合并结果。
                * 适用场景：适用于查询涉及多个实体或方面，需要分别检索不同信息的问题，例如：
                    * 示例：
                        * 查询：比较 Milvus 和 Zilliz Cloud 的优缺点。
                        * 策略：子查询检索
            4.  **回溯问题检索：**
                * 描述：将复杂的用户查询转化为更基础、更易于检索的问题，然后进行检索。
                * 适用场景：适用于查询较为复杂，需要简化后才能有效检索的问题，例如：
                    * 示例：
                        * 查询：我有一个包含 100 亿条记录的数据集，想把它存储到 Milvus 中进行查询。可以吗？
                        * 策略：回溯问题检索

            根据用户查询 {query}，直接返回最适合的策略名称，例如 "直接检索"。不要输出任何分析过程或其他内容。
            """
            ,
            input_variables=["query"],
        )


    # todo 1.4 定义方法，选择检索策略 -> 选择检索策略的核心方法 -> 整合模板和大模型调用, 返回最终策略.
    def select_strategy(self, query):
        """
        函数作用: 根据用户查询, 选择最合适的检索增强策略.
        :param query: 用户输入的查询文本(字符串)
        :return: 字符串 -> 选中的检索策略名称 -> 例如: 直接检索, 子查询检索...
        """
        # 1. 格式化提示模板: 将用户查询填充到提示模板的query为止, 生成发给大模型的完整提示, 调用大模型获取策略.
        strategy = self.call_dashscope(self.strategy_prompt_template.format(query=query)).strip()
        # 2. 记录日志.
        logger.info(f"为查询 '{query}' 选择的检索策略：{strategy}")
        # 3. 返回选中的策略.
        return strategy

if __name__ == '__main__':
    # 1. 实例化策略选择器
    ss = StrategySelector()
    # 2. 测试策略选择
    # ss.select_strategy('MySQL数据库能不能支持100W个样本的插入')
    # ss.select_strategy('对比北京和上海的人才引进政策, 从补贴金额, 落户难度, 产业适配性三个方面分析哪个更适合计算机专业毕业生')
    ss.select_strategy('如何培养孩子的时间管理能力')