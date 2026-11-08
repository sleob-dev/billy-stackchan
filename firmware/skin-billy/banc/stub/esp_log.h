#pragma once
#include <cstdio>
#define ESP_LOGE(tag, ...) (fprintf(stderr, __VA_ARGS__), fputc('\n', stderr))
#define ESP_LOGI(tag, ...) ((void)0)
