"""
Infra layer for buaa_msm.

说明：
- infra 负责 IO / 外部交互（文件系统、解密、cache repo 等）
- services 应尽量只依赖 infra（而不是直接依赖具体实现文件名，如 decrypt.py）
"""
