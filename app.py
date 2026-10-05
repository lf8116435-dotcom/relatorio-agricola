import io
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt
import simplekml
import streamlit as st
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
import warnings
from pyproj import CRS

from bs4 import XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

st.set_page_config(page_title="Relatórios de Testes Agrícolas", page_icon="🌱", layout="wide")

def extrair_dados_kml_preciso(html_desc):
    if not html_desc:
        return {}
    soup_desc = BeautifulSoup(html_desc, "html.parser")
    for br in soup_desc.find_all(["br", "div"]):
        br.append("\n")
    texto_puro = soup_desc.get_text(separator="")
    dados = {}
    for linha in texto_puro.split("\n"):
        linha_limpa = linha.replace("\xa0", " ").strip()
        if ":" in linha_limpa:
            partes = linha_limpa.split(":", 1)
            chave = partes[0].strip()
            valor = partes[1].strip()
            if chave and valor:
                dados[chave] = valor
    return dados

def calcular_area_ha(geometry):
    if geometry.geom_type == 'Point':
        return 0.0
    gdf_temp = gpd.GeoDataFrame(geometry=[geometry], crs="EPSG:4326")
    centroid = geometry.centroid
    utm_zone = int((centroid.x + 180) / 6) + 1
    epsg_utm = 31900 + utm_zone if centroid.y < 0 else 32600 + utm_zone
    try:
        CRS.from_epsg(epsg_utm)
    except Exception:
        epsg_utm = 31982
    gdf_utm = gdf_temp.to_crs(epsg=epsg_utm)
    return gdf_utm.geometry.area.iloc[0] / 10000.0

st.title("🌱 Sistema Automatizado de Relatórios de Testes Agrícolas")
st.markdown("Faça o upload do seu ficheiro **KML** e do **Shapefile Compactado (.zip)** para gerar os relatórios técnicos em PDF e KMLs individuais.")

col1, col2 = st.columns(2)
with col1:
    uploaded_kml = st.file_uploader("Ficheiro KML (.kml)", type=["kml"])
with col2:
    uploaded_zip = st.file_uploader("Shapefile da Fazenda (.zip)", type=["zip"])

if st.button("Gerar Relatórios", type="primary"):
    if uploaded_kml is not None and uploaded_zip is not None:
        with st.spinner("A processar os dados geográficos e a gerar os relatórios..."):
            
            os.makedirs("temp_data", exist_ok=True)
            path_kml = os.path.join("temp_data", uploaded_kml.name)
            path_zip = os.path.join("temp_data", uploaded_zip.name)
            
            with open(path_kml, "wb") as f:
                f.write(uploaded_kml.getbuffer())
            with open(path_zip, "wb") as f:
                f.write(uploaded_zip.getbuffer())

            try:
                gdf_kml = gpd.read_file(path_kml)
                if gdf_kml.crs is None:
                    gdf_kml.set_crs(epsg=4326, inplace=True)
                else:
                    gdf_kml = gdf_kml.to_crs(epsg=4326)

                gdf_fazenda = gpd.read_file(f"zip://{path_zip}")
                if gdf_fazenda.crs is None:
                    gdf_fazenda.set_crs(epsg=4326, inplace=True)
                else:
                    gdf_fazenda = gdf_fazenda.to_crs(epsg=4326)

                colunas_candidatas = ['NOME', 'TALHAO', 'Name', 'name', 'Id', 'ID', 'talhao', 'n_talhao', 'numero', 'NUMERO']
                col_talhao_shp = next((col for col in colunas_candidatas if col in gdf_fazenda.columns), gdf_fazenda.columns[0])

                with open(path_kml, "r", encoding="utf-8") as f:
                    conteudo_kml = f.read()

                soup_kml = BeautifulSoup(conteudo_kml, "xml")
                placemarks = soup_kml.find_all("Placemark")
                if not placemarks:
                    placemarks = soup_kml.find_all("placemark")

                arquivos_gerados = []

                for pm in placemarks:
                    nome_elem = pm.find("name")
                    nome_talhao = nome_elem.text.strip() if nome_elem else "Sem Nome"

                    if not re.search("test|camp", nome_talhao, re.IGNORECASE):
                        continue

                    match_kml = gdf_kml[gdf_kml["Name"] == nome_talhao]
                    if match_kml.empty:
                        match_kml = gdf_kml[gdf_kml["Name"].astype(str).str.contains(re.escape(nome_talhao), case=False, na=False)]

                    if not match_kml.empty:
                        geom_kml_row = match_kml.iloc[0]
                        geometria = geom_kml_row["geometry"]
                        numeros_encontrados = re.findall(r'\d+', nome_talhao)

                        geom_shp_combinado = None
                        if numeros_encontrados:
                            padrao_regex = '|'.join([r'\b' + num + r'\b' for num in numeros_encontrados])
                            filtro = gdf_fazenda[col_talhao_shp].astype(str).str.contains(padrao_regex, regex=True)
                            match_shp = gdf_fazenda[filtro]
                            if not match_shp.empty:
                                geom_shp_combinado = unary_union(match_shp.geometry.tolist())

                        desc_elem = pm.find("description")
                        desc_bruta = desc_elem.text if desc_elem else ""
                        dict_dados = extrair_dados_kml_preciso(desc_bruta)

                        if geometria.geom_type == 'Point':
                            if geom_shp_combinado is not None:
                                area_talhao = calcular_area_ha(geom_shp_combinado)
                                dict_dados["Área do Talhão"] = f"{area_talhao:.2f} ha"
                            else:
                                dict_dados["Área"] = "Ponto de Amostragem / Aplicação"
                        else:
                            area_calculada = calcular_area_ha(geometria)
                            dict_dados["Área"] = f"{area_calculada:.2f} ha"

                        # --- KML INDIVIDUAL ---
                        kml_indiv = simplekml.Kml()
                        if geometria.geom_type == 'Point':
                            kml_indiv.newpoint(name=nome_talhao, coords=[(geometria.x, geometria.y)])
                        else:
                            coords_poligono = list(geometria.exterior.coords)
                            kml_indiv.newpolygon(name=nome_talhao, outerboundaryis=coords_poligono)
                        
                        nome_arq_kml = os.path.join("temp_data", f"KML_{re.sub(r'[^a-zA-Z0-9]', '_', nome_talhao)}.kml")
                        kml_indiv.save(nome_arq_kml)

                        # --- IMAGEM CROQUI ---
                        fig, ax = plt.subplots(figsize=(6, 6))
                        if geom_shp_combinado is not None:
                            gdf_shp_temp = gpd.GeoDataFrame(geometry=[geom_shp_combinado], crs=gdf_fazenda.crs).to_crs(epsg=3857)
                            gdf_shp_temp.plot(ax=ax, color="#FFA500", edgecolor="#FFFFFF", alpha=0.25, linewidth=1.5)

                        gdf_kml_temp = gpd.GeoDataFrame([geom_kml_row], crs=gdf_kml.crs).to_crs(epsg=3857)
                        if geometria.geom_type == 'Point':
                            gdf_kml_temp.plot(ax=ax, color="#FF0000", marker='o', markersize=120, edgecolor="#FFFFFF", linewidth=1.5)
                        else:
                            gdf_kml_temp.plot(ax=ax, color="#00FF7F", edgecolor="#FFFFFF", alpha=0.55, linewidth=2.5)

                        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, crs=gdf_kml_temp.crs.to_string(), zoom=17, attribution=False)
                        ax.set_axis_off()
                        plt.tight_layout()
                        nome_img = os.path.join("temp_data", f"img_{abs(hash(nome_talhao))}.png")
                        plt.savefig(nome_img, dpi=300, bbox_inches="tight", facecolor="white")
                        plt.close()

                        # --- PDF ---
                        nome_pdf = os.path.join("temp_data", f"Relatorio_{re.sub(r'[^a-zA-Z0-9]', '_', nome_talhao)}.pdf")
                        from reportlab.lib.pagesizes import letter
                        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
                        from reportlab.lib import colors

                        doc = SimpleDocTemplate(nome_pdf, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
                        story = []
                        styles = getSampleStyleSheet()
                        
                        estilo_titulo = ParagraphStyle('TituloRelatorio', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=16, textColor=colors.HexColor("#1b5e20"), spaceAfter=4)
                        estilo_sub = ParagraphStyle('SubTituloRelatorio', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=colors.HexColor("#666666"), spaceAfter=12)
                        estilo_celula = ParagraphStyle('TextoTabela', parent=styles['Normal'], fontName='Helvetica', fontSize=10, textColor=colors.HexColor("#333333"))
                        estilo_celula_bold = ParagraphStyle('TextoTabelaBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#1b5e20"))
                        estilo_rodape = ParagraphStyle('TextoRodape', parent=styles['Normal'], fontName='Helvetica', fontSize=8, textColor=colors.HexColor("#888888"), alignment=1)

                        story.append(Paragraph("RELATÓRIO TÉCNICO DE TESTE EM CAMPO", estilo_titulo))
                        data_atual = datetime.now().strftime("%d/%m/%Y às %H:%M")
                        tipo_geom = "Ponto de Teste" if geometria.geom_type == 'Point' else "Polígono de Teste"
                        story.append(Paragraph(f"Item Analisado: <b>{nome_talhao}</b> | {tipo_geom} KML sobre Base SHP | Gerado em: {data_atual}", estilo_sub))
                        story.append(Spacer(1, 2))

                        img_croqui = Image(nome_img, width=340, height=340)
                        img_croqui.hAlign = 'CENTER'
                        story.append(img_croqui)
                        story.append(Spacer(1, 10))

                        dados_tabela = [[Paragraph("Parâmetro de Manejo", estilo_celula_bold), Paragraph("Especificação Técnica Registrada", estilo_celula_bold)]]
                        if dict_dados:
                            for chave, valor in dict_dados.items():
                                dados_tabela.append([Paragraph(f"<b>{chave}</b>", estilo_celula), Paragraph(valor, estilo_celula)])
                        else:
                            dados_tabela.append([Paragraph("Status", estilo_celula), Paragraph("Nenhuma especificação detalhada informada.", estilo_celula)])

                        tabela_info = Table(dados_tabela, colWidths=[180, 320])
                        tabela_info.hAlign = 'CENTER'
                        tabela_info.setStyle(TableStyle([
                            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#e8f5e9")),
                            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                            ('TOPPADDING', (0,0), (-1,-1), 6),
                            ('LEFTPADDING', (0,0), (-1,-1), 10),
                            ('RIGHTPADDING', (0,0), (-1,-1), 10),
                            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#c8e6c9")),
                            ('LINEBELOW', (0,0), (-1,0), 1.5, colors.HexColor("#2e7d32")),
                        ]))

                        story.append(tabela_info)
                        story.append(Spacer(1, 15))
                        story.append(Paragraph("Documento gerado automaticamente por automação agrícola inteligente", estilo_rodape))

                        doc.build(story)
                        if os.path.exists(nome_img):
                            os.remove(nome_img)

                        arquivos_gerados.append(nome_pdf)
                        arquivos_gerados.append(nome_arq_kml)

                if arquivos_gerados:
                    st.success(f"Processamento concluído com sucesso! Foram gerados {len(arquivos_gerados)//2} relatórios.")
                    st.markdown("### Ficheiros Prontos para Download:")
                    for arq in arquivos_gerados:
                        with open(arq, "rb") as f:
                            st.download_button(
                                label=f"Descarregar {os.path.basename(arq)}",
                                data=f,
                                file_name=os.path.basename(arq)
                            )
                else:
                    st.warning("Nenhum talhão correspondente ('teste' ou 'campo') foi encontrado no ficheiro KML.")

            except Exception as e:
                st.error(f"Ocorreu um erro durante o processamento: {e}")
    else:
        st.info("Por favor, carregue ambos os ficheiros (KML e o ZIP do Shapefile) para continuar.")