# LiteLLM Model Pricing Guide

> **Last Updated:** September 24, 2025
> **Source:** [LiteLLM GitHub Repository](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)

This comprehensive guide lists all models supported by LiteLLM with their pricing information, context windows, and features. All pricing is calculated per token unless otherwise specified.

## Table of Contents

1. [Amazon Bedrock Models](#amazon-bedrock-models)
2. [Azure OpenAI Models](#azure-openai-models)
3. [OpenAI Models](#openai-models)
4. [Anthropic Models](#anthropic-models)
5. [Image Generation Models](#image-generation-models)
6. [Embedding Models](#embedding-models)
7. [Audio Models](#audio-models)
8. [Reranking Models](#reranking-models)
9. [Anyscale Models](#anyscale-models)
10. [Azure AI Models](#azure-ai-models)
11. [Regional Pricing Variations](#regional-pricing-variations)

---

## Amazon Bedrock Models

### Amazon Nova Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `amazon.nova-micro-v1:0` | 128K | 10K | $0.035 | $0.14 | Function calling, Response schema |
| `amazon.nova-lite-v1:0` | 300K | 10K | $0.06 | $0.24 | Vision, PDF, Function calling, Prompt caching |
| `amazon.nova-pro-v1:0` | 300K | 10K | $0.80 | $3.20 | Vision, PDF, Function calling, Prompt caching |

### Amazon Titan Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Mode |
|-------|-----------|------------|--------------------|--------------------|------|
| `amazon.titan-text-lite-v1` | 42K | 4K | $0.30 | $0.40 | Chat |
| `amazon.titan-text-express-v1` | 42K | 8K | $1.30 | $1.70 | Chat |
| `amazon.titan-text-premier-v1:0` | 42K | 32K | $0.50 | $1.50 | Chat |

### Amazon Embeddings
| Model | Max Tokens | Input ($/1M tokens) | Vector Size | Features |
|-------|------------|--------------------|-----------|---------|
| `amazon.titan-embed-text-v1` | 8K | $0.10 | 1536 | Text embedding |
| `amazon.titan-embed-text-v2:0` | 8K | $0.20 | 1024 | Text embedding |
| `amazon.titan-embed-image-v1` | 128 | $0.80 | 1024 | Image + text embedding |

### Anthropic on Bedrock
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `anthropic.claude-3-haiku-20240307-v1:0` | 200K | 4K | $0.25 | $1.25 | Vision, PDF, Function calling |
| `anthropic.claude-3-sonnet-20240229-v1:0` | 200K | 4K | $3.00 | $15.00 | Vision, PDF, Function calling |
| `anthropic.claude-3-opus-20240229-v1:0` | 200K | 4K | $15.00 | $75.00 | Vision, Function calling |
| `anthropic.claude-3-5-sonnet-20240620-v1:0` | 200K | 4K | $3.00 | $15.00 | Vision, PDF, Function calling |
| `anthropic.claude-3-5-sonnet-20241022-v2:0` | 200K | 8K | $3.00 | $15.00 | Vision, PDF, Computer use, Prompt caching |
| `anthropic.claude-3-5-haiku-20241022-v1:0` | 200K | 8K | $0.80 | $4.00 | PDF, Function calling, Prompt caching |

### AI21 on Bedrock
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) |
|-------|-----------|------------|--------------------|--------------------|
| `ai21.j2-mid-v1` | 8K | 8K | $12.50 | $12.50 |
| `ai21.j2-ultra-v1` | 8K | 8K | $18.80 | $18.80 |
| `ai21.jamba-instruct-v1:0` | 70K | 4K | $0.50 | $0.70 |
| `ai21.jamba-1-5-mini-v1:0` | 256K | 256K | $0.20 | $0.40 |
| `ai21.jamba-1-5-large-v1:0` | 256K | 256K | $2.00 | $8.00 |

### Meta Llama on Bedrock
| Model | Region | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) |
|-------|--------|-----------|------------|--------------------|--------------------|
| `meta.llama3-8b-instruct-v1:0` | us-east-1 | 8K | 8K | $0.30 | $0.60 |
| `meta.llama3-70b-instruct-v1:0` | us-east-1 | 8K | 8K | $2.65 | $3.50 |
| `meta.llama3-8b-instruct-v1:0` | eu-west-1 | 8K | 8K | $0.32 | $0.65 |
| `meta.llama3-70b-instruct-v1:0` | eu-west-1 | 8K | 8K | $2.86 | $3.78 |

---

## Azure OpenAI Models

### GPT Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure/gpt-35-turbo` | 4K | 4K | $0.50 | $1.50 | Function calling |
| `azure/gpt-35-turbo-0125` | 16K | 4K | $0.50 | $1.50 | Function calling (deprecated) |
| `azure/gpt-4` | 8K | 4K | $30.00 | $60.00 | Function calling |
| `azure/gpt-4-turbo` | 128K | 4K | $10.00 | $30.00 | Vision, Function calling |
| `azure/gpt-4o` | 128K | 16K | $2.50 | $10.00 | Vision, Function calling, Prompt caching |
| `azure/gpt-4o-2024-08-06` | 128K | 16K | $2.50 | $10.00 | Vision, Function calling, Prompt caching |
| `azure/gpt-4o-2024-11-20` | 128K | 16K | $2.75 | $11.00 | Vision, Function calling, Prompt caching |
| `azure/gpt-4o-mini` | 128K | 16K | $0.165 | $0.66 | Vision, Function calling, Prompt caching |

### GPT-4.1 Models (Latest Generation)
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure/gpt-4.1` | 1M | 32K | $2.00 | $8.00 | Vision, Function calling, Prompt caching, Web search |
| `azure/gpt-4.1-mini` | 1M | 32K | $0.40 | $1.60 | Vision, Function calling, Prompt caching |
| `azure/gpt-4.1-nano` | 1M | 32K | $0.10 | $0.40 | Vision, Function calling, Prompt caching |

### GPT-5 Models (Preview)
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure/gpt-5` | 272K | 128K | $1.25 | $10.00 | Vision, Function calling, Reasoning, PDF |
| `azure/gpt-5-mini` | 272K | 128K | $0.25 | $2.00 | Vision, Function calling, Reasoning, PDF |
| `azure/gpt-5-nano` | 272K | 128K | $0.05 | $0.40 | Vision, Function calling, Reasoning, PDF |

### O-Series Models (Reasoning)
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure/o1` | 200K | 100K | $15.00 | $60.00 | Reasoning, Function calling, Prompt caching |
| `azure/o1-mini` | 128K | 65K | $1.21 | $4.84 | Reasoning, Function calling, Prompt caching |
| `azure/o1-preview` | 128K | 32K | $15.00 | $60.00 | Reasoning, Function calling, Prompt caching |
| `azure/o3` | 200K | 100K | $2.00 | $8.00 | Reasoning, Vision, Function calling |
| `azure/o3-mini` | 200K | 100K | $1.10 | $4.40 | Reasoning, Prompt caching |
| `azure/o3-pro` | 200K | 100K | $20.00 | $80.00 | Reasoning, Vision, Function calling |

### Audio Models
| Model | Mode | Input Cost | Output Cost | Features |
|-------|------|------------|-------------|----------|
| `azure/gpt-4o-audio-preview-2024-12-17` | Chat | $2.50/1M text + $40/1M audio | $10/1M text + $80/1M audio | Audio I/O |
| `azure/gpt-4o-mini-audio-preview-2024-12-17` | Chat | $2.50/1M text + $40/1M audio | $10/1M text + $80/1M audio | Audio I/O |
| `azure/whisper-1` | Transcription | $0.10/second | - | Audio transcription |

### Regional Pricing (Examples)
| Model | Region | Input ($/1M tokens) | Output ($/1M tokens) |
|-------|--------|--------------------|--------------------|
| `azure/global/gpt-4o-2024-08-06` | Global | $2.50 | $10.00 |
| `azure/us/gpt-4o-2024-08-06` | US | $2.75 | $11.00 |
| `azure/eu/gpt-4o-2024-08-06` | EU | $2.75 | $11.00 |

---

## Azure AI Models

### Meta Llama Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure_ai/Meta-Llama-3.1-8B-Instruct` | 128K | 2K | $0.30 | $0.61 | Function calling |
| `azure_ai/Meta-Llama-3.1-70B-Instruct` | 128K | 2K | $2.68 | $3.54 | Function calling |
| `azure_ai/Meta-Llama-3.1-405B-Instruct` | 128K | 2K | $5.33 | $16.00 | Function calling |
| `azure_ai/Llama-3.2-11B-Vision-Instruct` | 128K | 2K | $0.37 | $0.37 | Vision, Function calling |
| `azure_ai/Llama-3.2-90B-Vision-Instruct` | 128K | 2K | $2.04 | $2.04 | Vision, Function calling |

### Llama 4 Models (Latest)
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure_ai/Llama-4-Scout-17B-16E-Instruct` | 10M | 16K | $0.20 | $0.78 | Vision, Function calling |
| `azure_ai/Llama-4-Maverick-17B-128E-Instruct-FP8` | 1M | 16K | $1.41 | $0.35 | Vision, Function calling |

### Microsoft Phi Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure_ai/Phi-3-mini-128k-instruct` | 128K | 4K | $0.13 | $0.52 | Function calling |
| `azure_ai/Phi-3-small-128k-instruct` | 128K | 4K | $0.15 | $0.60 | Function calling |
| `azure_ai/Phi-3-medium-128k-instruct` | 128K | 4K | $0.17 | $0.68 | Function calling |
| `azure_ai/Phi-3.5-mini-instruct` | 128K | 4K | $0.13 | $0.52 | Function calling |
| `azure_ai/Phi-3.5-vision-instruct` | 128K | 4K | $0.13 | $0.52 | Vision, Function calling |
| `azure_ai/Phi-4` | 16K | 16K | $0.125 | $0.50 | Function calling |
| `azure_ai/Phi-4-mini-instruct` | 131K | 4K | $0.075 | $0.30 | Function calling |
| `azure_ai/Phi-4-multimodal-instruct` | 131K | 4K | $0.08 text + $4/1M audio | $0.32 | Vision, Audio, Function calling |

### Mistral Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure_ai/mistral-small` | 32K | 8K | $1.00 | $3.00 | Function calling |
| `azure_ai/mistral-large` | 32K | 8K | $4.00 | $12.00 | Function calling |
| `azure_ai/mistral-large-2407` | 128K | 4K | $2.00 | $6.00 | Function calling |
| `azure_ai/mistral-nemo` | 131K | 4K | $0.15 | $0.15 | Function calling |
| `azure_ai/ministral-3b` | 128K | 4K | $0.04 | $0.04 | Function calling |
| `azure_ai/mistral-medium-2505` | 131K | 8K | $0.40 | $2.00 | Function calling |
| `azure_ai/mistral-small-2503` | 128K | 128K | $1.00 | $3.00 | Vision, Function calling |

### DeepSeek Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure_ai/deepseek-v3` | 128K | 8K | $1.14 | $4.56 | Function calling |
| `azure_ai/deepseek-v3-0324` | 128K | 8K | $1.14 | $4.56 | Function calling |
| `azure_ai/deepseek-r1` | 128K | 8K | $1.35 | $5.40 | Reasoning, Function calling |

### Grok Models
| Model | Max Input | Max Output | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|-----------|------------|--------------------|--------------------|----------|
| `azure_ai/grok-3` | 131K | 131K | $3.30 | $16.50 | Web search, Function calling |
| `azure_ai/grok-3-mini` | 131K | 131K | $0.275 | $1.38 | Reasoning, Web search, Function calling |
| `azure_ai/global/grok-3` | 131K | 131K | $3.00 | $15.00 | Web search, Function calling |
| `azure_ai/global/grok-3-mini` | 131K | 131K | $0.25 | $1.27 | Reasoning, Web search, Function calling |

---

## Image Generation Models

### OpenAI DALL-E
| Model | Size | Input Cost per Image | Features |
|-------|------|---------------------|----------|
| `dall-e-2` | 256x256 | $0.016 | Basic generation |
| `dall-e-2` | 512x512 | $0.018 | Basic generation |
| `dall-e-2` | 1024x1024 | $0.020 | Basic generation |
| `dall-e-3` (standard) | 1024x1024 | $0.040 | Higher quality |
| `dall-e-3` (HD) | 1024x1024 | $0.080 | Premium quality |

### Azure DALL-E
| Model | Size/Quality | Input Cost per Image |
|-------|-------------|---------------------|
| `azure/standard/1024-x-1024/dall-e-3` | Standard | $0.040 |
| `azure/hd/1024-x-1024/dall-e-3` | HD | $0.080 |
| `azure/gpt-image-1` | Various | $0.011-0.164 |

### AI/ML API Models
| Model | Cost per Image | Notes |
|-------|---------------|-------|
| `aiml/dall-e-2` | $0.021 | Via AI/ML API |
| `aiml/dall-e-3` | $0.042 | Via AI/ML API |
| `aiml/flux-pro` | $0.053 | Professional grade |
| `aiml/flux/dev` | $0.026 | Development version |
| `aiml/flux/schnell` | $0.003 | Fast generation |

---

## Embedding Models

### OpenAI Embeddings
| Model | Max Tokens | Input ($/1M tokens) | Vector Size |
|-------|------------|--------------------|-----------|
| `text-embedding-ada-002` | 8K | $0.10 | 1536 |
| `text-embedding-3-small` | 8K | $0.020 | 1536 |
| `text-embedding-3-large` | 8K | $0.130 | 3072 |

### Azure Embeddings
| Model | Max Tokens | Input ($/1M tokens) | Vector Size |
|-------|------------|--------------------|-----------|
| `azure/text-embedding-ada-002` | 8K | $0.10 | 1536 |
| `azure/text-embedding-3-small` | 8K | $0.020 | 1536 |
| `azure/text-embedding-3-large` | 8K | $0.130 | 3072 |

### Azure AI Embeddings
| Model | Max Tokens | Input ($/1M tokens) | Vector Size | Features |
|-------|------------|--------------------|-----------|---------|
| `azure_ai/Cohere-embed-v3-english` | 512 | $0.10 | 1024 | Image support |
| `azure_ai/Cohere-embed-v3-multilingual` | 512 | $0.10 | 1024 | Image support |
| `azure_ai/embed-v-4-0` | 128K | $0.12 | 3072 | Image support |

---

## Audio Models

### OpenAI Audio Models
| Model | Mode | Cost | Features |
|-------|------|------|----------|
| `whisper-1` | Transcription | $0.006/minute | Speech-to-text |
| `tts-1` | Speech | $15.00/1M characters | Text-to-speech |
| `tts-1-hd` | Speech | $30.00/1M characters | High-quality TTS |

### Azure Audio Models
| Model | Mode | Cost | Features |
|-------|------|------|----------|
| `azure/whisper-1` | Transcription | $0.10/second | Speech-to-text |
| `azure/tts-1` | Speech | $15.00/1M characters | Text-to-speech |
| `azure/tts-1-hd` | Speech | $30.00/1M characters | High-quality TTS |

---

## Reranking Models

| Model | Provider | Cost per Query | Max Documents |
|-------|----------|----------------|--------------|
| `amazon.rerank-v1:0` | Bedrock | $1.00 | 100 |
| `azure_ai/cohere-rerank-v3-english` | Azure AI | $2.00 | - |
| `azure_ai/cohere-rerank-v3-multilingual` | Azure AI | $2.00 | - |
| `azure_ai/cohere-rerank-v3.5` | Azure AI | $2.00 | - |

---

## Anyscale Models

| Model | Max Tokens | Input ($/1M tokens) | Output ($/1M tokens) | Features |
|-------|------------|--------------------|--------------------|----------|
| `anyscale/meta-llama/Meta-Llama-3-8B-Instruct` | 8K | $0.15 | $0.15 | - |
| `anyscale/meta-llama/Meta-Llama-3-70B-Instruct` | 8K | $1.00 | $1.00 | - |
| `anyscale/mistralai/Mistral-7B-Instruct-v0.1` | 16K | $0.15 | $0.15 | Function calling |
| `anyscale/mistralai/Mixtral-8x7B-Instruct-v0.1` | 16K | $0.15 | $0.15 | Function calling |
| `anyscale/mistralai/Mixtral-8x22B-Instruct-v0.1` | 65K | $0.90 | $0.90 | Function calling |

---

## Regional Pricing Variations

### Bedrock Regional Pricing Examples

#### Anthropic Claude Models by Region
| Model | Region | Input ($/1M tokens) | Output ($/1M tokens) |
|-------|--------|--------------------|--------------------|
| `anthropic.claude-instant-v1` | us-east-1 | $0.80 | $2.40 |
| `anthropic.claude-instant-v1` | ap-northeast-1 | $2.23 | $7.55 |
| `anthropic.claude-instant-v1` | eu-central-1 | $2.48 | $8.38 |

#### Meta Llama by Region
| Model | Region | Input ($/1M tokens) | Output ($/1M tokens) |
|-------|--------|--------------------|--------------------|
| `meta.llama3-70b-instruct-v1:0` | us-east-1 | $2.65 | $3.50 |
| `meta.llama3-70b-instruct-v1:0` | eu-west-1 | $2.86 | $3.78 |
| `meta.llama3-70b-instruct-v1:0` | ca-central-1 | $3.05 | $4.03 |
| `meta.llama3-70b-instruct-v1:0` | ap-south-1 | $3.18 | $4.20 |

---

## Usage Notes

### Cost Calculation
- All token costs are per million tokens unless specified
- Image generation costs are per image
- Audio costs vary by second, minute, or character depending on the service
- Caching costs (when available) are additional to base token costs

### Model Features Legend
- **Function Calling**: Supports structured function/tool calling
- **Vision**: Can process images
- **PDF**: Can process PDF documents
- **Prompt Caching**: Supports prompt caching for cost savings
- **Reasoning**: Advanced reasoning capabilities
- **Web Search**: Can perform web searches
- **Audio I/O**: Audio input/output support
- **Computer Use**: Can interact with computer interfaces

### Regional Considerations
- Bedrock pricing varies significantly by AWS region
- Azure offers global, US, and EU specific pricing
- Some models are only available in specific regions
- Government cloud regions (us-gov) typically have different pricing

---

*This guide is auto-generated from the LiteLLM model pricing database. Prices and availability may change. Always verify current pricing with the respective providers.*
