import time
import xxhash 
import json
from typing import Dict, Any, Optional, List, Tuple
import logging
from collections import deque
from app.utils.logging import log
logger = logging.getLogger("my_logger")

# 定义缓存项的结构
CacheItem = Dict[str, Any]

class ResponseCacheManager:
    """管理API响应缓存的类，一个键可以对应多个缓存项（使用deque）"""
    
    def __init__(self, expiry_time: int, max_entries: int, 
                 cache_dict: Dict[str, deque[CacheItem]] = None):
        """
        初始化缓存管理器。
        
        Args:
            expiry_time (int): 缓存项的过期时间（秒）。
            max_entries (int): 缓存中允许的最大总条目数。
            cache_dict (Dict[str, deque[CacheItem]], optional): 初始缓存字典。默认为 None。
        """
        self.cache: Dict[str, deque[CacheItem]] = cache_dict if cache_dict is not None else {}
        self.expiry_time = expiry_time
        self.max_entries = max_entries # 总条目数限制
        self.cur_cache_num = 0 # 当前条目数
    
    def get(self, cache_key: str) -> Tuple[Optional[Any], bool]:
        """获取指定键的第一个有效缓存项（不删除）"""
        now = time.time()
        if cache_key in self.cache:
            cache_deque = self.cache[cache_key]
            # 查找第一个未过期的项，且不删除
            for item in cache_deque:
                if now < item.get('expiry_time', 0):
                    response = item.get('response',None)
                    
                    return response, True 

        return None, False

    def get_and_remove(self, cache_key: str) -> Tuple[Optional[Any], bool]:
        """获取并删除指定键的第一个有效缓存项。"""
        now = time.time()
        if cache_key in self.cache:
            cache_deque = self.cache[cache_key]
            
            # 查找第一个未过期的项，并删除它。顺便删除所有过期的项
            for item in cache_deque:
                if now < item.get('expiry_time', 0):
                    response = item.get('response',None)
                    self.cur_cache_num = max(0, self.cur_cache_num - 1)
                    cache_deque.remove(item)
                    
                    # 如果deque变空，则删除该键
                    if not cache_deque: 
                        del self.cache[cache_key]
                    
                    # 找到第一个就停止
                    return response, True 
                else:
                    cache_deque.remove(item)
                    self.cur_cache_num = max(0, self.cur_cache_num - 1)

        return None, False

    def store(self, cache_key: str, response: Any):
        """存储响应到缓存（追加到键对应的deque）"""
        now = time.time()
        new_item: CacheItem = {
            'response': response,
            'expiry_time': now + self.expiry_time,
            'created_at': now,
        }
        
        if cache_key not in self.cache:
            self.cache[cache_key] = deque()
        
        self.cache[cache_key].append(new_item) # 追加到deque末尾        
        
        self.cur_cache_num += 1 
        
        if self.cur_cache_num > self.max_entries:
            self.clean_if_needed()
    
    def clean_expired(self):
        """清理所有缓存项中已过期的项。"""
        now = time.time()
        keys_to_remove = []
        for key, cache_deque in self.cache.items():
            # 创建一个新的deque , 只包含未过期的项
            valid_items = deque(item for item in cache_deque if now < item.get('expiry_time', 0))
            
            if len(valid_items) < len(cache_deque):
                clean_num =len(cache_deque) - len(valid_items)
                log('info', f"清理键 {key[:8]}... 的过期缓存项 {clean_num} 个。")
                self.cur_cache_num = max(0, self.cur_cache_num - 1)
            if not valid_items:
                keys_to_remove.append(key) # 标记此键以便稍后删除
            else:
                self.cache[key] = valid_items # 替换为只包含有效项的deque
        
        # 删除所有项都已过期的键
        for key in keys_to_remove:
            del self.cache[key]
            log('info', f"缓存键 {key[:8]}... 的所有项均已过期，移除该键。")

    def clean_if_needed(self):
        """如果缓存总条目数超过限制，清理全局最旧的项目。"""
        if self.cur_cache_num <= self.max_entries:
            return

        items_to_remove_count = self.cur_cache_num - self.max_entries
        log('info', f"缓存总数 {self.cur_cache_num} 超过限制 {self.max_entries}，需要清理 {items_to_remove_count} 个最旧项。")

        # 收集所有缓存项及其元数据（键、创建时间、项本身）
        all_items_meta = []
        for key, cache_deque in self.cache.items():
            for item in cache_deque:
                all_items_meta.append({'key': key, 'created_at': item.get('created_at', 0), 'item': item})

        # 按创建时间排序（升序，最旧的在前）
        all_items_meta.sort(key=lambda x: x['created_at'])

        # 确定要删除的最旧项
        items_actually_removed = 0
        keys_potentially_empty = set()
        for i in range(min(items_to_remove_count, len(all_items_meta))):
            item_meta = all_items_meta[i]
            key_to_clean = item_meta['key']
            item_to_clean = item_meta['item']
            
            if key_to_clean in self.cache:
                try:
                    self.cache[key_to_clean].remove(item_to_clean)
                    items_actually_removed += 1
                    self.cur_cache_num = max(0, self.cur_cache_num - 1)
                    log('debug', f"因容量限制，删除键 {key_to_clean[:8]}... 的旧缓存项 (创建于 {item_meta['created_at']})。")
                    keys_potentially_empty.add(key_to_clean)
                except ValueError:
                     # 可能在处理过程中已被其他操作删除，忽略
                     log('warning', f"尝试因容量限制删除缓存项时未找到: {key_to_clean[:8]}...")
                     pass

        # 检查是否有deque变空
        for key in keys_potentially_empty:
             if key in self.cache and not self.cache[key]:
                 del self.cache[key]
                 log('info', f"因容量限制清理后，键 {key[:8]}... 的deque已空，移除该键。")
        
        if items_actually_removed > 0:
             log('info', f"因容量限制，共清理了 {items_actually_removed} 个旧缓存项。")


# 根据模型名称和全部消息，生成请求的唯一缓存键。
def generate_cache_key_all(chat_request) -> str:
    """
    根据模型名称和全部消息，生成请求的唯一缓存键。
    Args:
        chat_request: 包含模型和消息列表的请求对象 (符合OpenAI格式)。
    Returns:
        一个代表该请求的唯一缓存键字符串 (xxhash64哈希值)。
    """
    h = xxhash.xxh64()
    
    # 1. 哈希模型名称
    h.update(chat_request.model.encode('utf-8'))

    # 2. 增量哈希所有消息
    for msg in chat_request.messages:
        # 哈希角色
        h.update(b'role:')
        h.update(msg.role.encode('utf-8'))

        # 哈希内容
        if isinstance(msg.content, str):
            h.update(b'text:')
            h.update(msg.content.encode('utf-8'))
        elif isinstance(msg.content, list):
            # 处理图文混合内容
            for item in msg.content:
                item_type = item.get('type') if hasattr(item, 'get') else None
                if item_type == 'text':
                    text = item.get('text', '') if hasattr(item, 'get') else ''
                    h.update(b'text:') # 加入类型标识符
                    h.update(text.encode('utf-8'))
                elif item_type == 'image_url':
                    image_url = item.get('image_url', {}) if hasattr(item, 'get') else {}
                    image_data = image_url.get('url', '') if hasattr(image_url, 'get') else ''
                    
                    h.update(b'image_url:') # 加入类型标识符
                    if image_data.startswith('data:image/'):
                        # 对于base64图像，使用前32字符作为标识符
                        h.update(image_data[:32].encode('utf-8'))
                    else:
                        h.update(image_data.encode('utf-8'))
            
    return h.hexdigest()

def generate_cache_key(chat_request, last_n_user_messages: int = 8) -> str:
    """
    根据模型名称和最后 N 条消息生成请求的唯一缓存键。
    Args:
        chat_request: 包含模型和消息列表的请求对象 (符合OpenAI格式)。
        last_n_user_messages: 需要包含在缓存键计算中的最后消息的数量。
    Returns:
        一个代表该请求的唯一缓存键字符串 (xxhash64哈希值)。
    """
    h = xxhash.xxh64()
    
    # 1. 哈希模型名称
    h.update(chat_request.model.encode('utf-8'))

    if last_n_user_messages <= 0:
        # 如果不考虑消息，直接返回基于模型的哈希
        return h.hexdigest()

    messages_processed = 0
    # 2. 增量哈希最后 N 条消息 (从后往前)
    for msg in reversed(chat_request.messages):
        if messages_processed >= last_n_user_messages:
            break

        # 哈希角色
        h.update(b'role:')
        h.update(msg.role.encode('utf-8'))

        # 哈希内容
        if isinstance(msg.content, str):
            h.update(b'text:')
            h.update(msg.content.encode('utf-8'))
        elif isinstance(msg.content, list):
            # 处理图文混合内容
            for item in msg.content:
                item_type = item.get('type') if hasattr(item, 'get') else None
                if item_type == 'text':
                    text = item.get('text', '') if hasattr(item, 'get') else ''
                    h.update(b'text:') 
                    h.update(text.encode('utf-8'))
                elif item_type == 'image_url':
                    image_url = item.get('image_url', {}) if hasattr(item, 'get') else {}
                    image_data = image_url.get('url', '') if hasattr(image_url, 'get') else ''
                    
                    h.update(b'image_url:') # 加入类型标识符
                    if image_data.startswith('data:image/'):
                        # 对于base64图像，使用前32字符作为标识符
                        h.update(image_data[:32].encode('utf-8'))
                    else:
                        h.update(image_data.encode('utf-8'))

        messages_processed += 1
        
    return h.hexdigest()
