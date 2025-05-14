import os
import sys
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessing,
    QgsFeatureRequest,
    QgsGeometry,
    QgsFeature,
    QgsPointXY,
    QgsProcessingProvider,
    QgsApplication,
    QgsWkbTypes,
    QgsFields,
    QgsField,
    QgsVectorLayer,
    QgsProject,
    QgsProcessingUtils
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
import processing  # Импортируем модуль processing

sys.path.append(os.path.dirname(__file__))
import TopoCartGenCore

class TopoCartGenPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        self.provider = PluginProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        # Инициализация обработки
        self.initProcessing()

        # Путь к иконке
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        
        # Проверяем, существует ли файл иконки
        if os.path.exists(icon_path):
            print(f"Icon found at: {icon_path}")
            icon = QIcon(icon_path)
        else:
            print(f"Icon not found at: {icon_path}")
            icon = QIcon()  # Пустая иконка, если файл не найден

        # Создаём действие с иконкой
        self.action = QAction(icon, "Graph Simplification", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&TopoCartGen Tools", self.action)

    def unload(self):
        # Удаляем действие из панели инструментов и меню
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&TopoCartGen Tools", self.action)
            self.action = None

        # Удаляем провайдер обработки
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        # Запускаем диалог алгоритма через processing
        processing.execAlgorithmDialog("topocartgen:graphsimplify")

class PluginProcessingProvider(QgsProcessingProvider):
    def __init__(self):
        super().__init__()

    def id(self):
        return 'topocartgen'

    def name(self):
        return 'TopoCartGen Tools'

    def loadAlgorithms(self):
        self.addAlgorithm(GraphProcessorPlugin())

class GraphProcessorPlugin(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    RATIO = 'RATIO'

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUT,
                'Input layers',
                layerType=QgsProcessing.TypeVectorAnyGeometry
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RATIO,
                'Simplification ratio (0-1)',
                QgsProcessingParameterNumber.Double,
                0.5,
                False,
                0.0,
                1.0
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        input_layers = self.parameterAsLayerList(parameters, self.INPUT, context)
        ratio = self.parameterAsDouble(parameters, self.RATIO, context)
        feedback.pushInfo(f"Simplification ratio: {ratio}")

        if not input_layers:
            feedback.pushWarning("No input layers selected!")
            return {}

        # Подготовка выходных слоёв
        output_layers = {}
        layer_info = {}
        original_points_total = {}  # Счётчик точек в исходных слоях
        simplified_points_total = {}  # Счётчик точек в упрощённых слоях
        total_original_points = 0  # Общее количество точек в исходных слоях
        total_simplified_points = 0  # Общее количество точек в упрощённых слоях

        for layer in input_layers:
            source = layer.source()
            layer_name = source.split('layername=')[-1] if 'layername=' in source else layer.name()
            output_layer_name = f"{layer_name}_simplified"

            fields = layer.fields()
            wkb_type = layer.wkbType()
            if wkb_type in [QgsWkbTypes.Polygon]:
                output_wkb_type = QgsWkbTypes.MultiPolygon
            elif wkb_type in [QgsWkbTypes.LineString]:
                output_wkb_type = QgsWkbTypes.MultiLineString
            elif wkb_type in [QgsWkbTypes.Point]:
                output_wkb_type = QgsWkbTypes.MultiPoint
            else:
                output_wkb_type = wkb_type

            crs = layer.sourceCrs()
            wkb_type_str = QgsWkbTypes.displayString(output_wkb_type).lower()
            layer_uri = f"{wkb_type_str}?crs={crs.authid()}"
            output_layer = QgsVectorLayer(layer_uri, output_layer_name, "memory")
            output_layer.dataProvider().addAttributes(fields.toList())
            output_layer.updateFields()

            if not output_layer.isValid():
                feedback.pushWarning(f"Failed to create output layer for {output_layer_name}")
                continue

            context.temporaryLayerStore().addMapLayer(output_layer)
            output_layers[layer_name] = (output_layer, output_layer_name)
            layer_info[layer_name] = {
                'fields': fields,
                'wkb_type': output_wkb_type,
                'crs': crs,
                'geometry_type': layer.geometryType()
            }
            original_points_total[layer_name] = 0  # Инициализация счётчика для исходных точек
            simplified_points_total[layer_name] = 0  # Инициализация счётчика для упрощённых точек

        # Собираем данные из всех слоёв
        total_features = 0
        features_data = []
        original_features = {}
        original_points_count = {}

        for layer in input_layers:
            source = layer.source()
            layer_name = source.split('layername=')[-1] if 'layername=' in source else layer.name()
            geometry_type = layer_info[layer_name]['geometry_type']
            is_polygon = geometry_type == QgsProcessing.TypeVectorPolygon
            is_line = geometry_type == QgsProcessing.TypeVectorLine
            is_point = geometry_type == QgsProcessing.TypeVectorPoint

            layer_feature_count = layer.featureCount()
            total_features += layer_feature_count
            feedback.pushInfo(f"Layer {layer_name}: {layer_feature_count} features, geometry type = {QgsWkbTypes.displayString(layer.wkbType())}")

            for feature in layer.getFeatures():
                feature_id = feature.id()
                geometry = feature.geometry()
                points = []

                if geometry.isEmpty():
                    feedback.pushWarning(f"Layer {layer_name}, feature ID {feature_id}: empty geometry, skipping")
                    continue

                if is_polygon:
                    if geometry.isMultipart():
                        for part in geometry.asMultiPolygon():
                            for ring in part:
                                points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in ring])
                    else:
                        for ring in geometry.asPolygon():
                            points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in ring])
                elif is_line:
                    if geometry.isMultipart():
                        for line in geometry.asMultiPolyline():
                            points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in line])
                    else:
                        points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in geometry.asPolyline()])
                elif is_point:
                    if geometry.isMultipart():
                        points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in geometry.asMultiPoint()])
                    else:
                        points.append(TopoCartGenCore.Point(geometry.asPoint().x(), geometry.asPoint().y()))

                min_points = 4 if is_polygon else 2 if is_line else 1
                if len(points) < min_points:
                    feedback.pushWarning(f"Layer {layer_name}, feature ID {feature_id}: insufficient points ({len(points)}), using original geometry")
                    output_layer, _ = output_layers[layer_name]
                    success, _ = output_layer.dataProvider().addFeatures([feature])
                    if not success:
                        feedback.pushWarning(f"Layer {layer_name}, feature ID {feature_id}: failed to add original feature to output layer")
                    original_points_total[layer_name] += len(points)  # Считаем точки для исходного слоя
                    total_original_points += len(points)  # Обновляем общее количество точек
                    continue

                features_data.append((layer_name, feature_id, is_polygon, points))
                original_features[(layer_name, feature_id)] = feature
                original_points_count[(layer_name, feature_id)] = len(points)
                original_points_total[layer_name] += len(points)  # Считаем точки для исходного слоя
                total_original_points += len(points)  # Обновляем общее количество точек

        feedback.pushInfo(f"Total features: {total_features}, features to process: {len(features_data)}")

        if not features_data:
            feedback.pushWarning("No data to process! Output layers will be empty.")
            return {output_layer_name: output_layer.id() for _, (output_layer, output_layer_name) in output_layers.items()}

        # Упрощение всех геометрий
        feedback.pushInfo("Starting simplification with TopoCartGenCore.Graph")
        graph = TopoCartGenCore.Graph()
        simplified_features = graph.processFeatures(features_data, ratio)
        graph.clear()
        feedback.pushInfo(f"Simplified features: {len(simplified_features)}")

        # Распределение упрощённых объектов по выходным слоям
        added_features_count = {layer_name: 0 for layer_name in output_layers}
        processed_feature_ids = set()

        for layer_id, feature_id, is_polygon, points in simplified_features:
            processed_feature_ids.add((layer_id, feature_id))

            if layer_id not in output_layers:
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: no output layer found, skipping")
                continue

            output_layer, _ = output_layers[layer_id]
            layer_data = layer_info[layer_id]
            fields = layer_data['fields']
            output_wkb_type = layer_data['wkb_type']

            original_count = original_points_count.get((layer_id, feature_id), 0)
            is_closed = points and points[0] == points[-1]
            min_points = 4 if is_polygon else 2
            if len(points) < min_points:
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: insufficient points after simplification ({len(points)}), using original geometry")
                success, _ = output_layer.dataProvider().addFeatures([original_features[(layer_id, feature_id)]])
                if success:
                    added_features_count[layer_id] += 1
                else:
                    feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add original feature to output layer")
                simplified_points_total[layer_id] += original_count  # Считаем точки для исходного слоя, если упрощение не удалось
                total_simplified_points += original_count  # Обновляем общее количество упрощённых точек
                continue

            if is_polygon:
                if not is_closed and points:
                    points.append(points[0])
                point_list = [[QgsPointXY(p.getX(), p.getY()) for p in points]]
                geometry = QgsGeometry.fromPolygonXY(point_list)
            else:
                point_list = [QgsPointXY(p.getX(), p.getY()) for p in points]
                if output_wkb_type == QgsWkbTypes.MultiLineString:
                    geometry = QgsGeometry.fromMultiPolylineXY([point_list])
                else:
                    geometry = QgsGeometry.fromPolylineXY(point_list)

            if not geometry or geometry.isEmpty():
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: empty geometry after simplification, using original geometry")
                success, _ = output_layer.dataProvider().addFeatures([original_features[(layer_id, feature_id)]])
                if success:
                    added_features_count[layer_id] += 1
                else:
                    feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add original feature to output layer")
                simplified_points_total[layer_id] += original_count  # Считаем точки для исходного слоя, если упрощение не удалось
                total_simplified_points += original_count  # Обновляем общее количество упрощённых точек
                continue

            if output_wkb_type == QgsWkbTypes.MultiPolygon and geometry.wkbType() not in [QgsWkbTypes.Polygon, QgsWkbTypes.MultiPolygon]:
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: geometry type does not match output layer type MultiPolygon, using original geometry")
                success, _ = output_layer.dataProvider().addFeatures([original_features[(layer_id, feature_id)]])
                if success:
                    added_features_count[layer_id] += 1
                else:
                    feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add original feature to output layer")
                simplified_points_total[layer_id] += original_count  # Считаем точки для исходного слоя, если упрощение не удалось
                total_simplified_points += original_count  # Обновляем общее количество упрощённых точек
                continue

            if output_wkb_type == QgsWkbTypes.MultiPolygon and geometry.wkbType() == QgsWkbTypes.Polygon:
                polygon = geometry.asPolygon()
                multi_polygon = [polygon]
                geometry = QgsGeometry.fromMultiPolygonXY(multi_polygon)

            if not geometry.isGeosValid():
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: invalid geometry detected, attempting to fix")
                geometry = geometry.makeValid()
                if not geometry or geometry.isEmpty():
                    feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: geometry fix failed, using original geometry")
                    success, _ = output_layer.dataProvider().addFeatures([original_features[(layer_id, feature_id)]])
                    if success:
                        added_features_count[layer_id] += 1
                    else:
                        feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add original feature to output layer")
                    simplified_points_total[layer_id] += original_count  # Считаем точки для исходного слоя, если упрощение не удалось
                    total_simplified_points += original_count  # Обновляем общее количество упрощённых точек
                    continue

            original_feature = original_features[(layer_id, feature_id)]
            feat = QgsFeature(fields)
            feat.setId(feature_id)
            feat.setGeometry(geometry)
            feat.setAttributes(original_feature.attributes())

            success, _ = output_layer.dataProvider().addFeatures([feat])
            if success:
                added_features_count[layer_id] += 1
            else:
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add simplified feature to output layer, using original geometry")
                success, _ = output_layer.dataProvider().addFeatures([original_features[(layer_id, feature_id)]])
                if success:
                    added_features_count[layer_id] += 1
                else:
                    feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add original feature to output layer")
                simplified_points_total[layer_id] += original_count  # Считаем точки для исходного слоя, если упрощение не удалось
                total_simplified_points += original_count  # Обновляем общее количество упрощённых точек
                continue

            simplified_points_total[layer_id] += len(points)  # Считаем точки для упрощённого слоя
            total_simplified_points += len(points)  # Обновляем общее количество упрощённых точек

        for (layer_id, feature_id), feature in original_features.items():
            if (layer_id, feature_id) not in processed_feature_ids:
                feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: feature not simplified, using original geometry")
                output_layer, _ = output_layers[layer_id]
                fields = layer_info[layer_id]['fields']
                feat = QgsFeature(fields)
                feat.setId(feature_id)
                feat.setGeometry(feature.geometry())
                feat.setAttributes(feature.attributes())
                success, _ = output_layer.dataProvider().addFeatures([feat])
                if success:
                    added_features_count[layer_id] += 1
                else:
                    feedback.pushWarning(f"Layer {layer_id}, feature ID {feature_id}: failed to add original feature to output layer")
                # Подсчёт точек для неупрощённых объектов
                geometry = feature.geometry()
                points = []
                if geometry.wkbType() in [QgsWkbTypes.LineString, QgsWkbTypes.MultiLineString]:
                    if geometry.isMultipart():
                        for line in geometry.asMultiPolyline():
                            points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in line])
                    else:
                        points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in geometry.asPolyline()])
                elif geometry.wkbType() in [QgsWkbTypes.Polygon, QgsWkbTypes.MultiPolygon]:
                    if geometry.isMultipart():
                        for part in geometry.asMultiPolygon():
                            for ring in part:
                                points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in ring])
                    else:
                        for ring in geometry.asPolygon():
                            points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in ring])
                elif geometry.wkbType() in [QgsWkbTypes.Point, QgsWkbTypes.MultiPoint]:
                    if geometry.isMultipart():
                        points.extend([TopoCartGenCore.Point(p.x(), p.y()) for p in geometry.asMultiPoint()])
                    else:
                        points.append(TopoCartGenCore.Point(geometry.asPoint().x(), geometry.asPoint().y()))
                original_points_total[layer_id] += len(points)
                simplified_points_total[layer_id] += len(points)
                total_original_points += len(points)
                total_simplified_points += len(points)

        results = {}
        for layer_name, (output_layer, output_layer_name) in output_layers.items():
            output_layer.updateExtents()
            feature_count = output_layer.featureCount()
            feedback.pushInfo(f"Layer {layer_name}: {added_features_count[layer_name]} features written, actual count = {feature_count}")

            if output_layer.isValid():
                output_layer.setName(output_layer_name)
                QgsProject.instance().addMapLayer(output_layer, True)
                layer_tree = QgsProject.instance().layerTreeRoot()
                layer_node = layer_tree.findLayer(output_layer.id())
                if layer_node:
                    layer_node.setItemVisibilityChecked(True)

            # Вычисление процента упрощения для каждого слоя
            original_points = original_points_total[layer_name]
            simplified_points = simplified_points_total[layer_name]
            if original_points > 0:
                simplification_percentage = ((original_points - simplified_points) / original_points) * 100
                feedback.pushInfo(f"Layer {layer_name}: Original points = {original_points}, Simplified points = {simplified_points}, Simplification percentage = {simplification_percentage:.2f}%")
            else:
                feedback.pushInfo(f"Layer {layer_name}: No points to simplify")

            results[output_layer_name] = output_layer.id()

        # Вычисление общего процента упрощения
        if total_original_points > 0:
            total_simplification_percentage = ((total_original_points - total_simplified_points) / total_original_points) * 100
            feedback.pushInfo(f"Overall simplification: Original points = {total_original_points}, Simplified points = {total_simplified_points}, Total simplification percentage = {total_simplification_percentage:.2f}%")
        else:
            feedback.pushInfo("Overall simplification: No points to simplify across all layers")

        feedback.pushInfo(f"Processing completed with simplification ratio {ratio}")
        return results

    def name(self):
        return 'graphsimplify'

    def displayName(self):
        return 'Graph Simplification'

    def group(self):
        return 'Cartographic Generalization'

    def groupId(self):
        return 'cartogen'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GraphProcessorPlugin()