from utils.paths import PluginPaths

DATA_DIR = PluginPaths("poke_reply").data
TEXT_FILES_DIR = DATA_DIR / "text_files"
IMAGE_FILES_DIR = DATA_DIR / "image_files"
TEXT_TO_IMAGE_FILE = DATA_DIR / "text_to_image.json"
DELETE_REQUESTS_FILE = DATA_DIR / "delete_requests.json"

MAX_TEXT_LENGTH = 1000
# 文本相似度（difflib ratio）达到即判重复
TEXT_SIMILARITY_THRESHOLD = 0.6
# 图片 dHash 汉明距离不超过即进 MSE 复核，与 stickers 一致
HAMMING_THRESHOLD = 5
MSE_THRESHOLD = 50

DEFAULT_TEXT_TO_IMAGE_THRESHOLD = 200
MIN_TEXT_TO_IMAGE_THRESHOLD = 50

# 发出的投稿内容多久内可以申请删除、转文字
RECENT_MESSAGE_TTL = 10 * 60
PROCESSED_REQUEST_TTL = 24 * 60 * 60
PENDING_REQUEST_TTL = 7 * 24 * 60 * 60
