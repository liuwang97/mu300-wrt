
#ifndef SPRD_AUDIO_DMA_H
#define SPRD_AUDIO_DMA_H
#include <linux/dmaengine.h>
#include <linux/dma/sprd-dma.h>

struct sprd_dma_cfg {
	struct dma_slave_config config;
	struct sprd_dma_linklist ll_cfg;
	unsigned long  dma_config_flag;
	u32 transcation_len;
	u32 datawidth;
	u32 src_step;
	u32 des_step;
	u32 fragmens_len;
	u32 sg_num;
	/*
	 * MU300: the DMA request id, which 5.4 passed as dma_slave_config.slave_id (removed in 5.17); the vendor
	 * DMA driver (modules/sprd_audio/dma) takes it through peripheral_config, which points here. Before sg:
	 * the scatterlist array is laid out right after that pointer (sprd_pcm_hw_params).
	 */
	u32 req_id;
	struct scatterlist *sg;
};

#endif

